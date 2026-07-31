def acknowledge_alert(state, alert_id):
    state = {"acknowledged": list(state.get("acknowledged", []))}
    state["acknowledged"].append(alert_id)
    state["duplicate"] = False
    return state
