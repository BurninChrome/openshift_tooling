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
    try:
        pods = await api_client.list_pods(field_selector)
    except Exception as e:
        msgs.append(f"Error listing pods on node {node_name}: {str(e)}")
        return  # Skip deletion if we cannot list pods

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
