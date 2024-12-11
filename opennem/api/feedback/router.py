"""Feedback API endpoint"""

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi_versionizer import api_version

from opennem.core.feedback import UserFeedbackSubmission, persist_and_alert_user_feedback
from opennem.schema.opennem import OpennemBaseSchema

logger = logging.getLogger("opennem.api.feedback")

router = APIRouter()


@api_version(4)
@router.post("")
@router.post("/")
async def feedback_submissions(
    user_feedback: UserFeedbackSubmission,
    request: Request,
    # app_auth: AuthApiKeyRecord = Depends(get_api_key),  # type: ignore
    user_agent: str | None = Header(None),
) -> OpennemBaseSchema:
    """User feedback submission"""

    if not user_feedback.subject:
        raise HTTPException(status_code=400, detail="Subject is required")

    user_ip = request.client.host if request.client else "127.0.0.1"

    feedback = UserFeedbackSubmission(
        subject=user_feedback.subject,
        description=user_feedback.description,
        email=user_feedback.email,
        twitter=user_feedback.twitter,
        user_ip=user_ip,
        user_agent=user_agent,
    )

    await persist_and_alert_user_feedback(feedback)

    return OpennemBaseSchema()
