async def monitor_node(node_name, api_client, wait_time, check_interval, msgs, changed, end_time):
    """
    Monitors a single node during the update process.
    Now, it first waits for the node to actually start updating (change in desiredConfig).
    """

    # Step 1: Get initial configs
    try:
        node = await api_client.get_node(node_name)
        annotations = node['metadata'].get('annotations', {})
        initial_desired_config = annotations.get('machineconfiguration.openshift.io/desiredConfig')
        initial_current_config = annotations.get('machineconfiguration.openshift.io/currentConfig')
    except Exception as e:
        msgs.append(f"Error getting initial config for node {node_name}: {str(e)}")
        return

    # Step 2: Wait for the update to actually begin
    update_started = False
    while datetime.now(timezone.utc) < end_time:
        try:
            node = await api_client.get_node(node_name)
            annotations = node['metadata'].get('annotations', {})
            desired_config = annotations.get('machineconfiguration.openshift.io/desiredConfig')
            current_config = annotations.get('machineconfiguration.openshift.io/currentConfig')
            mc_state = annotations.get('machineconfiguration.openshift.io/state', None)

            # Check if the update started (desired_config changed from the initial)
            if desired_config != initial_desired_config:
                # Update has started
                update_started = True
                msgs.append(f"Node {node_name} started updating to {desired_config}.")
                break  # Exit this loop and proceed to monitoring the update process
        except Exception as e:
            msgs.append(f"Error monitoring node {node_name} for update start: {str(e)}")

        # Not started yet, wait and check again
        await asyncio.sleep(check_interval)

    # If we reach here without update_started being True, update never began
    if not update_started:
        msgs.append(f"Node {node_name} never started updating within the total duration.")
        return

    # Step 3: Now that the update has started, monitor progress (similar logic as before)
    # Keep checking states, handle 'Working', 'Degraded', etc.

    # Example of continued logic after update start:
    target_state_start_time = None
    node_in_target_state = False

    while datetime.now(timezone.utc) < end_time:
        try:
            node = await api_client.get_node(node_name)
            annotations = node['metadata'].get('annotations', {})
            desired_config = annotations.get('machineconfiguration.openshift.io/desiredConfig')
            current_config = annotations.get('machineconfiguration.openshift.io/currentConfig')
            mc_state = annotations.get('machineconfiguration.openshift.io/state', None)

            # Check if the node has successfully updated
            if desired_config == current_config and desired_config != initial_desired_config:
                msgs.append(f"Node {node_name} successfully updated to {desired_config}.")
                return

            # Check if the node is in a target state (Working/Degraded)
            if mc_state in ['Working', 'Degraded']:
                if not node_in_target_state:
                    node_in_target_state = True
                    target_state_start_time = datetime.now(timezone.utc)
                    msgs.append(f"Node {node_name} entered '{mc_state}' state.")
                else:
                    # Check how long it's been stuck
                    elapsed_time = (datetime.now(timezone.utc) - target_state_start_time).total_seconds()
                    if elapsed_time > wait_time:
                        # Attempt to delete pods
                        await attempt_pod_deletion(node_name, api_client, msgs, changed)
                        target_state_start_time = datetime.now(timezone.utc)
            else:
                if node_in_target_state:
                    # Node exited target state
                    node_in_target_state = False
                    msgs.append(f"Node {node_name} exited '{mc_state}' state.")

        except Exception as e:
            msgs.append(f"Error monitoring node {node_name}: {str(e)}")

        # Wait before checking again
        await asyncio.sleep(check_interval)

    # If we exit the loop due to timeout:
    if not changed[0]:
        msgs.append("No action taken.")
    else:
        msgs.append(f"Node {node_name} did not complete update within the total duration.")
