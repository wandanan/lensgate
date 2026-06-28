"""
Anthropic Messages API route handler — POST /v1/messages.

Parses the incoming Anthropic-format request body via the Format Detector,
returning a structured acknowledgment for Task B01 (full proxy pipeline
will be wired in later tasks).
"""

from fastapi import HTTPException, Request

from backend.src.format_detector import parse_anthropic_request


async def handle_anthropic_messages(request: Request) -> dict:
    """Handle POST /v1/messages — parse and acknowledge an Anthropic request.

    Returns:
        A dict summarising the parsed request on success.

    Raises:
        HTTPException 400: If the request body is not valid JSON or cannot be parsed.
    """
    # -- Parse JSON body -------------------------------------------------------
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_request"},
        )

    # -- Parse into canonical ProxyRequest -------------------------------------
    try:
        proxy_request = parse_anthropic_request(body)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": f"parse_error: {exc!s}"},
        )

    # -- Return acknowledgment (full proxy pipeline in later tasks) -------------
    image_count = sum(
        1
        for msg in proxy_request.messages
        for blk in msg.content
        if blk.__class__.__name__ == "ImageBlock"
    )

    return {
        "status": "parsed",
        "source_format": proxy_request.source_format,
        "target_model": proxy_request.target_model,
        "message_count": len(proxy_request.messages),
        "image_count": image_count,
        "stream": proxy_request.stream,
    }
