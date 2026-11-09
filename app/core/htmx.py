from fastapi import Request


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"
