# HackOTA 강사용 정답표

이 문서는 교육생 배포본에서 제외할 수 있습니다.

| 영역 | 의도된 취약점 | 기대 결과 |
| --- | --- | --- |
| Web | 로그인과 역할 검증 없음 | 누구나 업로드, 배포, 삭제 가능 |
| Web | CSRF 보호 없음 | 외부 페이지에서 배포 API 호출 가능 |
| Web | 확장자, MIME, 크기, 내용 검증 없음 | 임의 파일을 Firmware로 등록 |
| Web | 고정 Secret 및 Debug Mode | 내부 오류와 Debug 정보 노출 |
| Web | Broker 비밀번호를 Metadata와 로그에 저장 | Web/API/파일 접근 시 자격증명 노출 |
| Web/Update Server | 기본 MQTT 계정이 입력 기본값에 포함 | 추측 가능한 자격증명 |
| Web/Update Server | 전자서명과 해시 없음 | 저장된 Firmware 변조 후 정상 배포 |
| Web/Update Server | QoS 0, Retained Notice, 트랜잭션 결합 없음 | Notice/File 불일치 및 메시지 유실 가능 |
| MQTT | Anonymous, TLS/ACL 없음 | 구독을 통한 정보 노출과 임의 Publish |
| Gateway | Notice와 File의 일관성 검증 없음 | File Payload의 대상/버전을 그대로 신뢰 |
| Gateway | 알 수 없는 ECU를 Powertrain으로 Fallback | 잘못된 대상이 Powertrain으로 전달 |
| Gateway | 서명, 해시, 크기, Replay 검증 없음 | 변조, 반복, 크기 불일치 패키지 전달 |
| Training CAN | UDP 평문, 송신자 인증 없음 | 로컬에서 ECU Frame 위조 가능 |
| ECU | 송신자와 대상 ECU 검증 없음 | 포트로 도착한 모든 업데이트 수용 |
| ECU | Chunk 수, 누락, 중복, 크기 검증 없음 | 손상된 Firmware도 설치 상태로 전환 |
| ECU | Firmware 서명/해시 검증 없음 | 변조된 파일 설치 |
| ECU | 버전 비교와 Anti-rollback 없음 | 0.5.0 같은 구버전 설치 |
| ECU | 부팅 성공 확인 전 Slot 전환 | 불완전 Firmware도 Active 처리 |

## 권장 팀별 조합

* 초급: 무단 Web 배포 + Anti-rollback 부재
* 중급: MQTT 임의 Publish + Gateway 대상 검증 부재
* 중급: 저장 Firmware 변조 + 전 구간 서명 검증 부재
* 고급: UDP Data Packet 누락/순서 변경 + ECU Sequence 검증 부재
* 고급: Retained Notice와 별도 File을 조합한 메타데이터 불일치

## Solution 구현 우선순위

1. Firmware 서명과 Manifest 결합 검증
2. Web 인증, 역할 기반 배포 권한, CSRF
3. MQTT TLS/mTLS 및 Topic ACL
4. Gateway의 대상 ECU, Update ID, 크기, 해시, Replay 검증
5. ECU의 최종 서명, Sequence, 크기, Anti-rollback, 부팅 확인
