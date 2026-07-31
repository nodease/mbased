import concurrent.futures
import logging
import os
from typing import Any, Dict, Optional

import pymupdf4llm

from apps.workflow_engine.application.remote_file import (
    RemoteFileFetchError,
    RemoteFileFetcher,
)

from ..base.node import Node
from .entities import FileExtractionNodeData

# CPU 바운드 작업을 위한 전용 Executor (모듈 레벨 공유)
# max_workers는 서버 사양에 맞게 조절 (예: CPU 코어 수 * 2 등)
_cpu_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

logger = logging.getLogger(__name__)


class FileExtractionNode(Node[FileExtractionNodeData]):
    """
    문서 파일에서 텍스트를 추출하는 노드

    기능:
    - PDF 파일 경로를 받아서 텍스트 추출
    - S3 URL 또는 로컬 파일 경로 지원
    - pymupdf4llm을 사용하여 마크다운 형식으로 변환
    - 여러 변수 처리 및 중복 체크
    - 사용자가 정의한 이름으로 출력 변수 생성
    """

    node_type = "fileExtractionNode"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._remote_file_fetcher: RemoteFileFetcher | None = None

    def bind_remote_file_fetcher(self, fetcher: RemoteFileFetcher) -> None:
        self._remote_file_fetcher = fetcher

    def _run(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        문서 파일에서 텍스트를 추출합니다.

        [GEVENT] 동기 메서드로 변환 - gevent pool 호환성을 위해.
        I/O 작업(다운로드)은 주입된 guarded adapter를 사용하고,
        CPU 작업(PDF 변환)은 ThreadPoolExecutor를 사용합니다.

        Args:
            inputs: 이전 노드 결과 (변수 풀)

        Returns:
            사용자가 정의한 변수명으로 추출된 텍스트
            {
                "user_var1": "전체 텍스트...",
                "user_var2": "전체 텍스트..."
            }
        """
        if not self.data.referenced_variables:
            raise ValueError("파일 경로 변수를 선택해주세요.")

        # 각 변수에 대해 파일 추출 수행
        results = {}
        seen_names = set()  # 중복 체크용

        for variable in self.data.referenced_variables:
            # 출력 변수명 확인
            if not variable.name or not variable.name.strip():
                raise ValueError("출력 변수명을 입력해주세요.")

            output_name = variable.name.strip()

            # 중복 체크
            if output_name in seen_names:
                raise ValueError(f"중복된 변수명입니다: {output_name}")
            seen_names.add(output_name)

            # 파일 경로 추출
            file_path = self._extract_value_from_selector(
                variable.value_selector, inputs
            )

            # 파일 경로 확인
            if not file_path:
                raise ValueError(f"파일 경로를 찾을 수 없습니다: {output_name}")

            # 파일 준비 (S3 URL이면 다운로드, 로컬이면 경로 확인)
            if not isinstance(file_path, str):
                raise ValueError("파일 경로 형식이 올바르지 않습니다.")
            is_remote = file_path.startswith(("http://", "https://"))
            temp_file_path = None
            target_path = None

            try:
                if is_remote:
                    if self._remote_file_fetcher is None:
                        raise RemoteFileFetchError(
                            "remote_file.fetcher_unavailable"
                        )
                    temp_file_path = self._remote_file_fetcher.fetch_to_temp(file_path)
                    target_path = temp_file_path
                else:
                    # 로컬 파일 확인
                    if not os.path.exists(file_path):
                        raise FileNotFoundError("파일을 찾을 수 없습니다.")
                    target_path = file_path

                # 문서 텍스트 추출 (CPU Bound -> ThreadPoolExecutor)
                # pymupdf4llm은 CPU를 많이 사용하므로 별도 스레드 풀에서 실행
                future = _cpu_executor.submit(
                    self._extract_text_sync,
                    target_path,
                    output_name,
                    file_path,
                )
                full_text = future.result()  # 동기적으로 결과 대기
                results[output_name] = full_text

            finally:
                # 임시 파일 정리
                if temp_file_path and os.path.exists(temp_file_path):
                    try:
                        os.remove(temp_file_path)
                    except Exception as exc:
                        logger.warning(
                            "Temporary file cleanup failed: error_type=%s",
                            type(exc).__name__,
                        )

        return results

    def _extract_text_sync(
        self, target_path: str, output_name: str, original_path: str
    ) -> str:
        """
        실제 PDF 파싱을 수행하는 동기 함수 (CPU Bound).
        별도의 Executor에서 실행되어야 합니다.
        """
        try:
            md_text_chunks = pymupdf4llm.to_markdown(target_path, page_chunks=True)
            return "\n\n".join([chunk["text"] for chunk in md_text_chunks])
        except Exception:
            raise ValueError("문서 파싱에 실패했습니다.") from None

    def _extract_value_from_selector(
        self, selector: list[str], inputs: Dict[str, Any]
    ) -> Optional[str]:
        """
        value_selector를 사용하여 값을 추출합니다.

        Args:
            selector: [node_id, output_key] 형식의 선택자
            inputs: 이전 노드 결과 (변수 풀)

        Returns:
            추출된 값, 없으면 None
        """
        if not selector or len(selector) < 1:
            return None

        # 첫 번째 요소: 노드 ID
        target_node_id = selector[0]
        source_data = inputs.get(target_node_id)

        if source_data is None:
            return None

        # 두 번째 요소가 있으면: 특정 키 추출
        if len(selector) > 1:
            if isinstance(source_data, dict):
                return source_data.get(selector[1])
            else:
                return None
        else:
            # 노드 ID만 있으면 전체 데이터 반환
            return source_data
