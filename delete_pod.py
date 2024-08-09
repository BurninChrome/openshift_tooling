from kubernetes import client
from kubernetes.client import ApiClient, V1DeleteOptions

def delete_pod(api_client: ApiClient, namespace: str, pod_name: str, force: bool = True) -> None:
    """
    Forcefully deletes a specific pod in a given namespace. If the pod uses an emptyDir volume,
    the data will be lost upon deletion.

    Parameters:
    - api_client: ApiClient - The Kubernetes API client.
    - namespace: str - The namespace where the pod is located.
    - pod_name: str - The name of the pod to be deleted.
    - force: bool - Whether to forcefully kill the pod (default is True).

    Raises:
    - Exception: If there is an issue deleting the pod.
    """
    try:
        # Create a CoreV1Api instance using the provided API client
        v1 = client.CoreV1Api(api_client)

        # Set the deletion options, including forceful termination if requested
        delete_options = V1DeleteOptions()

        if force:
            # Set the grace period to 0 to forcefully delete the pod
            delete_options.grace_period_seconds = 0

        # Delete the specified pod with the given delete options
        v1.delete_namespaced_pod(name=pod_name, namespace=namespace, body=delete_options)

        print(f"Pod '{pod_name}' in namespace '{namespace}' has been forcefully deleted.")
        print(f"Note: If the pod was using an emptyDir volume, all data in that volume has been lost.")

    except client.exceptions.ApiException as e:
        if e.status == 404:
            raise Exception(f"Pod '{pod_name}' in namespace '{namespace}' not found.")
        else:
            raise Exception(f"Failed to delete pod '{pod_name}' in namespace '{namespace}': {e}")
    except Exception as e:
        raise Exception(f"An unexpected error occurred: {e}")
