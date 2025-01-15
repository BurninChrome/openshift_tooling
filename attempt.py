async def attempt_pod_deletion(node_name, api_client, msgs, changed):
    """
    Attempts to forcefully delete pods on the node.

    Parameters:
    - node_name: Name of the node.
    - api_client: Kubernetes API client.
    - msgs: List to collect messages.
    - changed: List indicating if changes were made.
    """
    try:
        await delete_pods_on_node(node_name, api_client, msgs, changed)
    except Exception as e:
        msgs.append(f"Error deleting pods on node {node_name}: {str(e)}")
