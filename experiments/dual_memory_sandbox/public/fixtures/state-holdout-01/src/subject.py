def reserve_seat(event, request_id):
    event = {"remaining": event["remaining"], "reservations": list(event.get("reservations", []))}
    event["remaining"] -= 1
    event["reservations"].append(request_id)
    return event
