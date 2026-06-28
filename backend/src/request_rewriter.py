"""
Request Rewriter — replaces image blocks with text recognition results.

Handles images at both top-level message content AND nested inside
tool_result blocks (Claude Code's Read tool embeds images there).
"""

from __future__ import annotations

import copy

from backend.src.models import ImageBlock, ProxyRequest, TextBlock


class RequestRewriter:
    """Replace ImageBlocks in a ProxyRequest with text recognition descriptions."""

    def rewrite(
        self,
        request: ProxyRequest,
        vision_results: list[tuple[ImageBlock, str]],
    ) -> ProxyRequest:
        """Rewrite the request, replacing image blocks with text descriptions.

        Handles images at both top-level message content and nested inside
        tool_result blocks (via ImageBlock.parent_block_index).
        """
        if not vision_results:
            return request

        total = len(vision_results)

        for i, (img, description) in enumerate(vision_results, start=1):
            text = f"[图片内容]\n{description}"
            msg_idx = img.message_index
            blk_idx = img.block_index
            parent_idx = img.parent_block_index

            # --- Update internal canonical form ---------------------------------
            if parent_idx is not None:
                # Image is nested inside a tool_result block.
                request.messages[msg_idx].content[parent_idx].content[blk_idx] = (
                    TextBlock(text=text)
                )
            else:
                request.messages[msg_idx].content[blk_idx] = TextBlock(text=text)

        # --- Update original_body -----------------------------------------------
        request.original_body = _build_rewritten_body(
            request.original_body, vision_results
        )

        return request


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_rewritten_body(
    body: dict,
    vision_results: list[tuple[ImageBlock, str]],
) -> dict:
    """Build a new body dict with image blocks replaced by text blocks.

    Navigates both top-level content and nested tool_result.content based
    on ImageBlock.parent_block_index.
    """
    body = copy.deepcopy(body)
    total = len(vision_results)

    for i, (img, description) in enumerate(vision_results, start=1):
        text = f"[图片内容]\n{description}"
        text_block = {"type": "text", "text": text}
        msg_idx = img.message_index
        blk_idx = img.block_index
        parent_idx = img.parent_block_index

        if parent_idx is not None:
            body["messages"][msg_idx]["content"][parent_idx]["content"][blk_idx] = text_block
        else:
            body["messages"][msg_idx]["content"][blk_idx] = text_block

    return body
