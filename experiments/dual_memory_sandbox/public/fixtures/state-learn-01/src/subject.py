def apply_payment(account, payment_id, amount):
    account = {"balance": account["balance"], "seen_payments": list(account.get("seen_payments", []))}
    account["balance"] -= amount
    account["seen_payments"].append(payment_id)
    return account
