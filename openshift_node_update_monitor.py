async def monitor_node(node_name, api_client, wait_time, check_interval, msgs, changed, end_time):
    """
    Monitors a single node during the update process.
    This version will attempt to get the initial node state for up to 5 minutes before giving up.
    """

    # We define a 5-minute retry window for fetching the initial node state
    initial_retry_duration = 300  # 5 minutes in seconds
    initial_retry_end_time = datetime.now(timezone.utc) + timedelta(seconds=initial_retry_duration)

    initial_desired_config = None
    initial_current_config = None
    update_started = False
    node_in_target_state = False
    target_state_start_time = None

    # Step 1: Try to get the initial node configs until we succeed or time out
    while datetime.now(timezone.utc) < initial_retry_end_time:
        try:
            node = await api_client.get_node(node_name)
            annotations = node['metadata'].get('annotations', {})
            initial_desired_config = annotations.get('machineconfiguration.openshift.io/desiredConfig')
            initial_current_config = annotations.get('machineconfiguration.openshift.io/currentConfig')
            # Successfully got the node state, break out of the loop
            break
        except Exception as e:
            msgs.append(f"Error getting initial config for node {node_name}: {str(e)}")
            # Wait before trying again
            await asyncio.sleep(check_interval)

    # After trying for 5 minutes, check if we got the initial configs
    if initial_desired_config is None or initial_current_config is None:
        # We never got a valid initial node state
        msgs.append(f"Could not retrieve initial node state for {node_name} after 5 minutes. Aborting.")
        return

    # Step 2: Wait for the update to actually begin
    while datetime.now(timezone.utc) < end_time:
        try:
            node = await api_client.get_node(node_name)
            annotations = node['metadata'].get('annotations', {})
            desired_config = annotations.get('machineconfiguration.openshift.io/desiredConfig')
            current_config = annotations.get('machineconfiguration.openshift.io/currentConfig')
            mc_state = annotations.get('machineconfiguration.openshift.io/state', None)

            # Check if the update started
            if not update_started and desired_config != initial_desired_config:
                update_started = True
                msgs.append(f"Node {node_name} started updating to {desired_config}.")

            if not update_started:
                # Node has not started updating yet, just keep waiting
                await asyncio.sleep(check_interval)
                continue

            # Check if the node has successfully updated
            if desired_config == current_config and desired_config != initial_desired_config:
                msgs.append(f"Node {node_name} successfully updated to {desired_config}.")
                return

            # Check 'Working' or 'Degraded' states
            if mc_state in ['Working', 'Degraded']:
                if not node_in_target_state:
                    node_in_target_state = True
                    target_state_start_time = datetime.now(timezone.utc)
                    msgs.append(f"Node {node_name} entered '{mc_state}' state.")
                else:
                    # Check how long it has been in the target state
                    elapsed_time = (datetime.now(timezone.utc) - target_state_start_time).total_seconds()
                    if elapsed_time > wait_time:
                        # Attempt to delete pods
                        await attempt_pod_deletion(node_name, api_client, msgs, changed)
                        # Reset the timer after intervention
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

    # If we reach here, time is up without success
    if not update_started:
        msgs.append(f"Node {node_name} never started updating within the total duration.")
    else:
        msgs.append(f"Node {node_name} did not complete the update within the total duration.")
