#!/usr/bin/python
# -*- coding: utf-8 -*-

# Ansible module to monitor OpenShift 4.10+ nodes during the update process.
# Continuously watches nodes for state changes and forcefully deletes pods if necessary to allow updates to proceed.

DOCUMENTATION = r'''
---
module: openshift_node_update_monitor

short_description: Continuously monitor OpenShift nodes during updates and forcefully delete stuck pods.

version_added: "1.0.0"

description:
    - Continuously monitors OpenShift 4.10+ nodes during the update process.
    - Watches for nodes entering the 'Working' state and waits for a specified time before forcefully deleting pods.
    - If the node does not restart after pod deletion, repeats the process until the node successfully reboots.
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
    check_interval:
        description:
            - Time in seconds between checks when watching nodes.
        required: false
        type: int
        default: 60
    total_duration:
        description:
            - Maximum total time in seconds for the module to run. Set to 0 for unlimited duration.
        required: false
        type: int
        default: 3600  # 1 hour by default

author:
    - Your Name (@yourgithubhandle)
'''

EXAMPLES = r'''
# Monitor nodes using cluster URL and API token
- name: Continuously monitor OpenShift Nodes During Update
  openshift_node_update_monitor:
    cluster_url: "https://api.cluster.example.com:6443"
    api_token: "{{ lookup('env', 'K8S_API_TOKEN') }}"
    wait_time: 600
    check_interval: 60
    total_duration: 3600
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
from datetime import datetime, timezone, timedelta
import ssl
import urllib3

# Disable SSL warnings if verify_ssl is False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

async def monitor_node(node_name, api_client, wait_time, check_interval, msgs, changed, end_time):
    """
    Asynchronously monitor a single node, watching for state changes and taking action if necessary.

    Parameters:
    - node_name: The name of the node to monitor.
    - api_client: The API client for Kubernetes API calls.
    - wait_time: The time to wait after a node enters 'Working' state before deleting pods.
    - check_interval: The interval between checks when watching the node.
    - msgs: A list to collect messages about actions taken.
    - changed: A list containing a boolean indicating if any changes were made.
    - end_time: The time at which the monitoring should stop.
    """
    node_in_working_state = False
    working_state_start_time = None

    while datetime.now(timezone.utc) < end_time:
        # Get the current state of the node
        node = await api_client.get_node(node_name)
        labels = node['metadata'].get('labels', {})
        mc_state = labels.get('machineconfiguration.openshift.io/state', None)

        if mc_state == 'Working':
            if not node_in_working_state:
                # Node has just entered 'Working' state
                node_in_working_state = True
                working_state_start_time = datetime.now(timezone.utc)
                msgs.append(f"Node {node_name} entered 'Working' state.")
            else:
                # Node is still in 'Working' state
                elapsed_time = (datetime.now(timezone.utc) - working_state_start_time).total_seconds()
                if elapsed_time > wait_time:
                    # Time to delete pods
                    await delete_pods_on_node(node_name, api_client, msgs, changed)
                    # Reset the working state start time to wait again
                    working_state_start_time = datetime.now(timezone.utc)
        else:
            if node_in_working_state:
                # Node has exited 'Working' state
                node_in_working_state = False
                msgs.append(f"Node {node_name} exited 'Working' state.")

        # Check if the node is ready (i.e., has restarted)
        conditions = node['status'].get('conditions', [])
        ready_condition = next((cond for cond in conditions if cond['type'] == 'Ready'), None)
        if ready_condition and ready_condition['status'] == 'True':
            if node_in_working_state:
                # Node is ready, so it has restarted
                node_in_working_state = False
                msgs.append(f"Node {node_name} has restarted and is ready.")
                # Exit the monitoring loop for this node
                break

        # Wait for the check interval before checking again
        await asyncio.sleep(check_interval)

async def delete_pods_on_node(node_name, api_client, msgs, changed):
    """
    Asynchronously delete pods on a node.

    Parameters:
    - node_name: The name of the node.
    - api_client: The API client for Kubernetes API calls.
    - msgs: A list to collect messages about actions taken.
    - changed: A list containing a boolean indicating if any changes were made.
    """
    field_selector = f"spec.nodeName={node_name}"
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

    async def get_node(self, node_name):
        """
        Asynchronously get a specific node.
        """
        url = f"{self.api_server}/api/v1/nodes/{node_name}"
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
        await self.async_request('DELETE', url, params=params)

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
    check_interval = module.params['check_interval']
    total_duration = module.params['total_duration']

    # Calculate the end time for monitoring
    if total_duration > 0:
        end_time = datetime.now(timezone.utc) + timedelta(seconds=total_duration)
    else:
        # If total_duration is 0, run indefinitely (not recommended for Ansible modules)
        end_time = datetime.max.replace(tzinfo=timezone.utc)

    # Initialize the Kubernetes API client
    api_client = KubernetesAPIClient(module)

    # Initialize variables to track changes and messages
    overall_changed = [False]  # List to allow modification within async functions
    overall_msgs = []

    try:
        # Asynchronously get all nodes in the cluster
        nodes_response = await api_client.list_nodes()
        nodes = nodes_response.get('items', [])

        # List of node names to monitor
        node_names = [node['metadata']['name'] for node in nodes]

        # Create a list of tasks for monitoring each node concurrently
        tasks = [
            monitor_node(node_name, api_client, wait_time, check_interval, overall_msgs, overall_changed, end_time)
            for node_name in node_names
        ]

        # Run all tasks concurrently and wait for them to complete or for the total duration to elapse
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
            check_interval=dict(type='int', required=False, default=60),  # Default check interval of 60 seconds
            total_duration=dict(type='int', required=False, default=3600),  # Default total duration of 1 hour
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
