#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
Ansible module to monitor OpenShift 4.10+ nodes during the update process.
If a node is stuck in the update process for more than 10 minutes due to pods not being deleted,
the module forcefully deletes those pods to allow the update to proceed.

Author: Your Name
Date: YYYY-MM-DD
"""

from ansible.module_utils.basic import AnsibleModule
from kubernetes import client, config, asyncio_client
import asyncio
from datetime import datetime
import ssl

async def process_node(node, v1, wait_time, msgs, changed):
    """
    Asynchronously process a single node to check if it's stuck in the update process
    and forcefully delete pods if necessary.

    Parameters:
    - node: The node object to process.
    - v1: The CoreV1Api client for Kubernetes API calls.
    - wait_time: The maximum allowed time (in seconds) for a node to be in 'Working' state.
    - msgs: A list to collect messages about actions taken.
    - changed: A list containing a boolean indicating if any changes were made.
    """
    node_name = node.metadata.name  # Get the node's name
    labels = node.metadata.labels   # Get the node's labels

    # Check if the node is in the process of updating (state 'Working')
    mc_state = labels.get('machineconfiguration.openshift.io/state', None)

    if mc_state == 'Working':
        # Node is being updated
        # Get annotations to find the last update time
        annotations = node.metadata.annotations
        last_update_time_str = annotations.get('machineconfiguration.openshift.io/last-update-state-time', None)

        if last_update_time_str:
            # Parse the last update time from the annotation
            last_update_time = datetime.strptime(last_update_time_str, '%Y-%m-%dT%H:%M:%SZ')
            elapsed_time = datetime.utcnow() - last_update_time  # Calculate how long the node has been updating

            if elapsed_time.total_seconds() > wait_time:
                # Node has been updating for more than the allowed wait_time
                # Identify pods that are preventing the node from draining
                field_selector = 'spec.nodeName={}'.format(node_name)
                # Asynchronously list all pods scheduled on this node
                pods = await v1.list_pod_for_all_namespaces(field_selector=field_selector)

                for pod in pods.items:
                    # Skip pods that are already terminating
                    if pod.metadata.deletion_timestamp:
                        continue

                    # Force delete the pod to unblock the node update process
                    try:
                        await v1.delete_namespaced_pod(
                            name=pod.metadata.name,
                            namespace=pod.metadata.namespace,
                            body=client.V1DeleteOptions(grace_period_seconds=0),
                        )
                        changed[0] = True  # Indicate that a change was made
                        msgs.append(f"Forcefully deleted pod {pod.metadata.name} in namespace {pod.metadata.namespace} on node {node_name}.")
                    except Exception as e:
                        # Log any exceptions during pod deletion
                        msgs.append(f"Failed to delete pod {pod.metadata.name}: {str(e)}")
        else:
            # Last update time is missing; log a message
            msgs.append(f"Cannot determine last update time for node {node_name}")

async def main_async(module):
    """
    Asynchronous main function to execute the module logic.

    Parameters:
    - module: The AnsibleModule instance containing parameters and methods.
    """
    # Retrieve parameters from the module
    kubeconfig = module.params['kubeconfig']
    context = module.params['context']
    wait_time = module.params['wait_time']
    verify_ssl = module.params['verify_ssl']
    cluster_url = module.params['cluster_url']
    api_token = module.params['api_token']

    try:
        # Configure Kubernetes API client based on provided parameters
        if cluster_url and api_token:
            # Create a configuration object
            configuration = client.Configuration()
            configuration.host = cluster_url  # Set the cluster URL
            configuration.verify_ssl = verify_ssl  # Set SSL verification
            if not verify_ssl:
                configuration.ssl_ca_cert = None  # Disable CA certificate checking
            configuration.api_key = {"authorization": f"Bearer {api_token}"}  # Set the API token
            # Create an asynchronous API client using the configuration
            api_client = asyncio_client.ApiClient(configuration=configuration)
            v1 = asyncio_client.CoreV1Api(api_client=api_client)  # Core V1 API for Kubernetes
        elif kubeconfig:
            # Load from a kubeconfig file
            config.load_kube_config(config_file=kubeconfig, context=context)
            configuration = client.Configuration.get_default_copy()
            configuration.verify_ssl = verify_ssl
            api_client = asyncio_client.ApiClient(configuration=configuration)
            v1 = asyncio_client.CoreV1Api(api_client=api_client)
        else:
            # Load in-cluster configuration
            config.load_incluster_config()
            configuration = client.Configuration.get_default_copy()
            configuration.verify_ssl = verify_ssl
            api_client = asyncio_client.ApiClient(configuration=configuration)
            v1 = asyncio_client.CoreV1Api(api_client=api_client)

    except Exception as e:
        # Fail the module if configuration loading fails
        module.fail_json(msg=f"Failed to load Kubernetes configuration: {str(e)}")

    # Initialize variables to track changes and messages
    overall_changed = [False]  # List to allow modification within async functions
    overall_msgs = []

    try:
        # Asynchronously get all nodes in the cluster
        nodes = await v1.list_node()

        # Create a list of tasks for processing each node concurrently
        tasks = [
            process_node(node, v1, wait_time, overall_msgs, overall_changed)
            for node in nodes.items
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
            kubeconfig=dict(type='str', required=False, default=None),
            context=dict(type='str', required=False, default=None),
            wait_time=dict(type='int', required=False, default=600),  # Default wait time of 600 seconds (10 minutes)
            verify_ssl=dict(type='bool', required=False, default=True),
            cluster_url=dict(type='str', required=False, default=None),
            api_token=dict(type='str', required=False, no_log=True),
        ),
        supports_check_mode=False  # This module does not support check mode
    )

    # Run the asynchronous main function using asyncio
    asyncio.run(main_async(module))

if __name__ == '__main__':
    main()
