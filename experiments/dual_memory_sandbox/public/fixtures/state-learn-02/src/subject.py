def mark_shipped(order):
    order = {"status": order["status"], "history": list(order.get("history", []))}
    order["status"] = "shipped"
    order["history"].append("shipped")
    return order
