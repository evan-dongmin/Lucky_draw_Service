# Third Party Notices

이 프로젝트는 다음 오픈소스 패키지를 사용합니다. 각 패키지의 라이선스 전문은
해당 프로젝트의 공식 저장소/PyPI 페이지에서 확인할 수 있습니다.

| 패키지 | 버전 | 라이선스 | 용도 |
|---|---|---|---|
| fastapi | 0.115.6 | MIT | 웹 서버 프레임워크, REST API·WebSocket 라우팅 |
| uvicorn[standard] | 0.34.0 | BSD-3-Clause | ASGI 서버 |
| websockets | 14.1 | BSD-3-Clause | WebSocket 프로토콜 구현 (uvicorn 의존) |
| openpyxl | 3.1.5 | MIT | xlsx 명단 파싱 |
| qrcode[pil] | 8.0 | BSD-3-Clause | 모바일 참여 화면(/mobile) 접속용 QR 코드 생성 |
| openai | 2.53.0 | Apache-2.0 | AI MC 멘트 사전 생성 -- xAI Grok 호출용 클라이언트(OpenAI 호환 API, 선택적) |
| google-genai | 2.17.0 | Apache-2.0 | AI MC 멘트 사전 생성 -- Gemini 호출용 클라이언트(Grok 실패 시 대체, 선택적) |
| pydantic | 2.12.4 | MIT | FastAPI 요청/응답 모델 검증 |
| python-multipart | 0.0.20 | Apache-2.0 | 명단 파일 업로드(multipart/form-data) 처리 |
| pytest | 8.3.4 | MIT | 테스트 프레임워크 |
| pytest-asyncio | 0.25.2 | Apache-2.0 | 비동기(asyncio) 테스트 지원 |

프런트엔드(static/)는 순수 HTML/CSS/JavaScript로 작성되었으며 외부
라이브러리·CDN·빌드 도구에 의존하지 않습니다. 공정성 검증 페이지
(verify.html)는 브라우저 표준 Web Crypto API(SubtleCrypto)를 사용하며,
이는 브라우저 내장 기능으로 별도 라이선스 고지가 필요한 외부 코드가 아닙니다.
