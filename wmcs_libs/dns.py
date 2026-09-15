def format_srv_record(*, priority: int, weight: int, port: int, target: str):
    if not target.endswith("."):
        raise ValueError(f"DNS target '{target}' must be a fully qualified domain name ending with a period")
    return f"{priority} {weight} {port} {target}"
