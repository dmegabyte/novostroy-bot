def add_vote(poll, voter_id):
    poll = {"total": poll["total"], "voters": list(poll.get("voters", []))}
    poll["total"] += 1
    poll["voters"].append(voter_id)
    return poll
