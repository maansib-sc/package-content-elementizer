from fastapi import UploadFile
import requests
import io
from typing import Callable, Optional, Dict, Any, Union
from talkingdb.models.api.mode import ClientMode
from talkingdb.models.failure.failure import DocumentFailure
from talkingdb.models.failure.reason import FailureReason
from talkingdb.models.metadata.metadata import Metadata
from talkingdb.helpers.client import Config
from ..api import reader


class CEClient:
    def __init__(
        self,
        config: Config,
        headers: Optional[Dict[str, str]] = None,
    ):

        self.mode = config.CLIENT_MODE
        self.timeout = config.API_TIMEOUT
        self.endpoint = config.CE_HOST

        self.session: Optional[requests.Session] = None

        if self.mode == ClientMode.API:
            if not self.endpoint:
                raise ValueError("endpoint is required in API mode")

            self.session = requests.Session()
            if headers:
                self.session.headers.update(headers)

    async def parse_file(
        self,
        *,
        file_bytes: Optional[Union[io.BytesIO, bytes]] = None,
        file_name: Optional[str] = None,
        file: Optional[UploadFile] = None,
        metadata: Optional[Metadata] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        checkpoint_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`cancel_check` and `checkpoint_dir` are supported only in DIRECT mode.
        API mode runs remotely, so local checkpoints and cooperative cancellation are not supported."""

        if self.mode == ClientMode.API:
            if file:
                file_name = file.filename
                file_bytes = file.file.read()

            if isinstance(file_bytes, bytes):
                file_bytes = io.BytesIO(file_bytes)

            files = {"document_file": (file_name, file_bytes)}
            data = {}

            if metadata:
                data["metadata"] = metadata.to_str()

            response = self.session.post(
                f"{self.endpoint}/content/parse",
                files=files,
                data=data,
                timeout=self.timeout
            )
            if response.status_code >= 400:
                self._raise_declared_failure(response)
                response.raise_for_status()
            return response.json()

        if not file:
            file = UploadFile(filename=file_name, file=file_bytes)

        return await reader.run_parse(
            document_file=file,
            metadata=metadata.to_str() if metadata else None,
            cancel_check=cancel_check,
            checkpoint_dir=checkpoint_dir,
        )

    # ------------------------------------------------------------- boundary
    @staticmethod
    def _raise_declared_failure(response) -> None:
        try:
            body = response.json()
        except ValueError:
            return

        if not isinstance(body, dict):
            return

        raw_reason = body.get("failure_reason")
        if not raw_reason:
            return

        try:
            reason = FailureReason(raw_reason)
        except ValueError:
            return

        raise DocumentFailure(reason, detail=body.get("detail"))
