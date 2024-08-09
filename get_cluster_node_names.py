from kubernetes import client
from kubernetes.client import ApiClient
from typing import List

def get_cluster_node_names(api_client: ApiClient) -> List[str]:
    """
    Returns a list of all node names in the Kubernetes cluster.

    Parameters:
    - api_client: ApiClient - The Kubernetes API client.

    Returns:
    - List[str]: A list of node names in the cluster.
    
    Raises:
    - Exception: If there is an issue retrieving the node names.
    """
    try:
        # Create a CoreV1Api instance using the provided API client
        v1 = client.CoreV1Api(api_client)

        # List all nodes in the cluster
        nodes = v1.list_node()

        # Extract and return the node names
        node_names = [node.metadata.name for node in nodes.items]
        return node_names

    except Exception as e:
        raise Exception(f"Failed to retrieve node names: {e}")

