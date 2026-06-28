"""
Route handlers for the multimodal proxy gateway.

Each submodule handles a single API endpoint and delegates parsing to
the Format Detector.  The handlers are thin wrappers — they extract the
JSON body, call the appropriate parser, and return an acknowledgment.
Later tasks (image extraction, forwarding) will extend these handlers.
"""
