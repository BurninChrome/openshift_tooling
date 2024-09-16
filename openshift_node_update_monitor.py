#!/usr/bin/python
# -*- coding: utf-8 -*-

# Ansible module to monitor OpenShift 4.10+ nodes during the update process.
# If a node is stuck in the update process for more than a specified time (default is 10 minutes) due to pods not being deleted,
# the module forcefully deletes those pods to allow the update to proceed.

DOCUMENTATION = r'''
---
module: openshift_node_update_monitor

short_description: Monitor OpenShift nodes during updates and forcefully delete stuck pods.

version_added: "1.0.0"

description:
    - Monitors OpenShift 4.10+ nodes during the update process.
    - If a node is stuck in the update process for more than a specified time due to pods not being deleted, it forcefully deletes those pods.
    - Uses asyncio for concurrent processing of nodes.
    - Uses the requests library to interact with the Kubernetes API server.
    - The kubeconfig is created from parameters like cluster URL and user token.

options:
    cluster_url:
        description:
            - The Kubernetes API server URL.
        required: true
        type: str
    api_token:
        description:
            - The API token for authentication with the Kubernetes API server.
        required: true
        type: str
        no_log: true
    verify_ssl:
        description:
            - Whether to verify SSL certificates when connecting to the Kubernetes API server.
        required: false
        type: bool
        default: true
    wait_time:
        description:
            - Time in seconds to wait before forcefully deleting pods on a node stuck in the update process.
        required: false
        type: int
        default: 600

author:
    - Your Name (@yourgithubhandle)
'''

EXAMPLES = r'''
# Monitor nodes using cluster URL and API token
- name: Monitor OpenShift Nodes During Update
  openshift_node_update_monitor:
    cluster_url: "https://api.cluster.example.com:6443"
    api_token: "{{ lookup('env', 'K8S_API_TOKEN') }}"
    wait_time: 600
    verify_ssl: False
'''

RETURN = r'''
msg:
    description: A message describing the result of the module execution.
    type: str
    returned: always
changed:
    description: Indicates if any changes were made (pods forcefully deleted).
    type: bool
    returned: always
'''

from ansible.module_utils.basic import AnsibleModule
import requests
import asyncio
from datetime import datetime, timezone
import ssl
import urllib3

# Disable SSL warnings if verify_ssl is False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

async def process_node(node, api_client, wait_time, msgs, changed):
    """
    Asynchronously process a single node to check if it's stuck in the update process
    and forcefully delete pods if necessary.

    Parameters:
    - node: The node object to process.
    - api_client: The API client containing session and configuration.
    - wait_time: The maximum allowed time (in seconds) for a node to be in 'Working' state.
    - msgs: A list to collect messages about actions taken.
    - changed: A list containing a boolean indicating if any changes were made.
    """
    node_name = node['metadata']['name']  # Get the node's name
    labels = node['metadata'].get('labels', {})   # Get the node's labels

    # Check if the node is in the process of updating (state 'Working')
    mc_state = labels.get('machineconfiguration.openshift.io/state', None)

    if mc_state == 'Working':
        # Node is being updated
        # Get annotations to find the last update time
        annotations = node['metadata'].get('annotations', {})
        last_update_time_str = annotations.get('machineconfiguration.openshift.io/last-update-state-time', None)

        if last_update_time_str:
            # Parse the last update time from the annotation
            last_update_time = datetime.strptime(last_update_time_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            elapsed_time = datetime.now(timezone.utc) - last_update_time  # Calculate how long the node has been updating

            if elapsed_time.total_seconds() > wait_time:
                # Node has been updating for more than the allowed wait_time
                # Identify pods that are preventing the node from draining
                field_selector = f"spec.nodeName={node_name}"
                # Asynchronously list all pods scheduled on this node
                pods = await api_client.list_pods(field_selector)

                for pod in pods.get('items', []):
                    # Skip pods that are already terminating
                    if pod['metadata'].get('deletionTimestamp'):
                        continue

                    pod_name = pod['metadata']['name']
                    namespace = pod['metadata']['namespace']

                    # Force delete the pod to unblock the node update process
                    try:
                        await api_client.delete_pod(namespace, pod_name)
                        changed[0] = True  # Indicate that a change was made
                        msgs.append(f"Forcefully deleted pod {pod_name} in namespace {namespace} on node {node_name}.")
                    except Exception as e:
                        # Log any exceptions during pod deletion
                        msgs.append(f"Failed to delete pod {pod_name}: {str(e)}")
        else:
            # Last update time is missing; log a message
            msgs.append(f"Cannot determine last update time for node {node_name}")

class KubernetesAPIClient:
    """
    A simple Kubernetes API client using requests.
    """

    def __init__(self, module):
        self.module = module
        self.verify_ssl = module.params['verify_ssl']
        self.session = requests.Session()
        self.session.verify = self.verify_ssl
        self.api_server = module.params['cluster_url']
        self.token = module.params['api_token']

        if not self.api_server or not self.token:
            self.module.fail_json(msg="cluster_url and api_token are required parameters.")

        # Set the authorization header
        self.session.headers.update({'Authorization': f'Bearer {self.token}'})

        # Set the Content-Type header
        self.session.headers.update({'Content-Type': 'application/json'})

    async def list_nodes(self):
        """
        Asynchronously list all nodes in the cluster.
        """
        url = f"{self.api_server}/api/v1/nodes"
        response = await self.async_request('GET', url)
        return response.json()

    async def list_pods(self, field_selector):
        """
        Asynchronously list pods based on a field selector.
        """
        url = f"{self.api_server}/api/v1/pods"
        params = {'fieldSelector': field_selector}
        response = await self.async_request('GET', url, params=params)
        return response.json()

    async def delete_pod(self, namespace, pod_name):
        """
        Asynchronously delete a pod.
        """
        url = f"{self.api_server}/api/v1/namespaces/{namespace}/pods/{pod_name}"
        params = {'gracePeriodSeconds': '0'}
        response = await self.async_request('DELETE', url, params=params)
        return response

    async def async_request(self, method, url, **kwargs):
        """
        Asynchronous wrapper for making HTTP requests.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.request, method, url, **kwargs)

    def request(self, method, url, **kwargs):
        """
        Synchronous HTTP request using the session.
        """
        try:
            response = self.session.request(method, url, **kwargs)
            if not response.ok:
                self.module.fail_json(msg=f"API request to {url} failed with status code {response.status_code}: {response.text}")
            return response
        except Exception as e:
            self.module.fail_json(msg=f"API request to {url} failed: {str(e)}")

async def main_async(module):
    """
    Asynchronous main function to execute the module logic.

    Parameters:
    - module: The AnsibleModule instance containing parameters and methods.
    """
    wait_time = module.params['wait_time']

    # Initialize the Kubernetes API client
    api_client = KubernetesAPIClient(module)

    # Initialize variables to track changes and messages
    overall_changed = [False]  # List to allow modification within async functions
    overall_msgs = []

    try:
        # Asynchronously get all nodes in the cluster
        nodes_response = await api_client.list_nodes()
        nodes = nodes_response.get('items', [])

        # Create a list of tasks for processing each node concurrently
        tasks = [
            process_node(node, api_client, wait_time, overall_msgs, overall_changed)
            for node in nodes
        ]

        # Run all tasks concurrently and wait for them to complete
        await asyncio.gather(*tasks)

    except Exception as e:
        # Fail the module if node processing fails
        module.fail_json(msg=f"Failed to process nodes: {str(e)}")

    # If no messages were collected, indicate that no action was taken
    if not overall_msgs:
        overall_msgs.append("No action taken.")

    # Exit the module, returning whether changes were made and any messages
    module.exit_json(changed=overall_changed[0], msg='\n'.join(overall_msgs))

def main():
    """
    The main entry point for the Ansible module.
    """
    # Define the module's argument specifications
    module = AnsibleModule(
        argument_spec=dict(
            cluster_url=dict(type='str', required=True),
            api_token=dict(type='str', required=True, no_log=True),
            verify_ssl=dict(type='bool', required=False, default=True),
            wait_time=dict(type='int', required=False, default=600),  # Default wait time of 600 seconds (10 minutes)
        ),
        supports_check_mode=False  # This module does not support check mode
    )

    # Run the asynchronous main function using asyncio
    try:
        asyncio.run(main_async(module))
    except Exception as e:
        module.fail_json(msg=f"Module execution failed: {str(e)}")

if __name__ == '__main__':
    main()
