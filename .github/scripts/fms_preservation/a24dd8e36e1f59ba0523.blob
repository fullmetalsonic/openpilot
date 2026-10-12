# FMS 선별 통합 운영 계약

## 상태와 방향

2026-10-12 사용자 결정: FMS는 원본의 동작과 같아지는 것을 목표로 하는 자동 동기화 브랜치가 아니다. **현재 FMS의 동작 계약을 유지하며 필요한 원본 변경을 선별 이식하는 포크**로 관리한다. 설정 몇 개를 바꾸는 수준의 패치만으로 해석하지 않는다.

현재 승인 범위는 최신 원본의 검토된 비DM 변경을 로컬에 통합하고 검증하는 것이다. 커밋·푸시, 기기 설치/설정 변경, 안정판/DDM 갱신, 예약 재개는 승인 범위에 없다. 통합 검증 뒤 Astra Extra High가 실제 코드와 검증 근거를 최종 검토한다.

- FMS 동작/DM/개인 패치 기준: `dca1f33137b8f24dfd23e35bc2bc3d9fd21c5d69`.
- 원본 기반: `ef586a41637a00968e4521aeb99021fe609b5105`.
- 이번 검토 대상: `3f537bc13fdc325f4ef3852f8ab5a762566cefc1`.
- 안정판 유지: `609cecef51011d893e252cf61079ba6b90faabc4`.
- 원본 보관 DDM 유지: `ef586a41637a00968e4521aeb99021fe609b5105`.

Git 병합은 이력과 충돌을 추적하는 도구다. 원본의 모든 정책을 채택한다는 뜻이 아니다. 혼합 커밋/파일에서는 채택할 비DM 변경과 제외할 DM/정지 정책을 분리하며, 넓은 revert나 과거 공유 파일 덮어쓰기로 비DM 변경을 잃지 않는다. 향후 SHA가 달라지면 기존 계약 영향과 의존 변경부터 다시 검토한다.

## FMS 소유 계약

| 영역 | 반드시 보존할 동작 |
| --- | --- |
| DM | 현재 DisableDM0/1/2·기본0·재시작 적용, 설정/백업/QR/profile부터 프로세스·영상·모델/daemon·감시/events·forceDecel·UI/소리·CAN까지 기존 전체 동작. ef586a4 이후 새 DM 동작·설정·전용 런타임 변경 미채택 |
| 독립 SoftHold | 비 PCM OFF/대기/CANCEL에서도 실제 CAN 정차. hold 자체의 activateCruise/buttonEnable/RES/자동 ON 요청 없음 |
| 취소·자동크루즈 | CANCEL press 즉시 latch, CANCEL에서 gas로 hold 해제 뒤 자동 ON 금지. 정상 ON 정차/재출발과 CANCEL 아닌 대기의 기존 자동 ON 정책 유지. CruiseOnDist0은 거리 조건만 끔 |
| CANFD 정지 | 현행 일반 ACC 조절형 정지와 독립 hold 음수 준비/유지, 두 builder와 실제 이전 출력 연결을 한 묶음으로 보존. unavailable은 일반 차단·독립만 예외, 기존 페달/주차/CAN/기어/조향/종방향 인터록 유지 |
| 패들 | 0~4·좌우 방향·1~4 경계·press당 한 번·모드 재시작 정책. 모드4 간격 입력은 실제 ON/활성 long의 새 press만 접수. OFF/대기/CANCEL/독립 hold에서 회생 조작 시 gap 저장/큐/재ON 재생 없음 |
| 패들 출력 | 지정 MQ4 HEV camera-SCC OP-long 정상 ACC의 제한 예외·SCC2 연속성. 내부 OEM cancel과 raw 운전자 CANCEL 구분. 페달/override/invalid/최종 CC 차단/apply 생략/긴 공백/epoch에서 즉시 철회, 새 유효 release 필요 |
| 간격 저장 | 정상 ON 입력의 늦은 비동기 저장 허용. 직접 gap 버튼/웹의 대기 변경 유지. 큐 전체 삭제/과거 값 복원으로 정상 요청을 잃지 않음 |
| 방향지시등 | LEFT_BLINK_HOLD=0, RIGHT_BLINK_HOLD=0만 유지. 차선변경/조향/경로에 새 개인 수정 없음 |
| VEgoStopping | 최소1·최대100·기본50·단위5, get_float×0.01과 저장값 유지. manager/planner/modeld의 10 강제 보정·비동기 저장 없음. 모든 값을1로 강제하지 않음 |
| Change Branch | 원본+fullmetalsonic 목록·선택 브랜치만 다운로드·origin/체크아웃/설정 불변 |
| 퇴역/운영 | 개인 205개 최초설정·first_install_defaults·marker 재도입 금지. 자동화 중지, stable/DDM 및 기기/Params 불변 |

## DM 동결의 접합 경계

새 DM을 넣고 OFF로 쓰는 방식은 기존 DM 보존이 아니다. 새 Enabled/Mode/Vision 설정 이행, fallback/session OFF/CANCEL 3회/touch/preview/DM 경고 주입/전용 cadence·camera async 보호를 채택하지 않는다. 기존 DisableDM1/2에서는 DriverView도 DM model/daemon을 강제로 켜지 않는다. 현행 forceDecel은 DisableDM0에서 alert3 또는 softDisabling일 때 true이고, 1/2에서는 false다. 이를 모든 통신/프로세스 오류 무시로 확대하지 않는다.

최신 비DM wire 번호를 보존하기 위한 CarState @94와 OnroadEvent @126의 DEPRECATED 비활성 예약은 새 DM 실행과 구분한다. 생산자/소비자는 없어야 하며 비DM BSD/systemReady/Impact 번호를 당기지 않는다. 단일 최종 schema로 바인딩을 맞춘다. 신호 관찰기의 GPU 자동 선택은 구 DisableDM==0의 사용을 보수적으로 예약하도록 최소 접합하고, 기존 DM 자체를 변경하지 않는다.

## 정지 제어 보존의 접합 경계

`2608792b`의 zero-aReq/복구 FSM 삭제와 `82290515`의 첫 StopReq jerkUpper 변경은 정지 범위만 분리해 제외한다. 원본이 ECU에서 실패한다는 판정이 아니라, 현행 FMS 출력 계약을 유지하기 위한 선택이다.

- `stopping.py`의 현행 prepare/request/release/retry/fallback/held 코어와 a_ego/value/previous_value/independent_hold 의미를 유지한다.
- `hyundaicanfd.py`의 두 builder에서 원래 enabled 기준 일반/독립 분리와 인터록, 실제 aReq 반환/전달을 보존한다. gate 예외만 복구해서는 음수 준비/유지가 돌아오지 않는다.
- `carcontroller.py`의 stopping 생성자/호출/실제 출력 feedback과 패들 문맥을 최신 비DM 코드에 접합한다. 직전 출력을0으로 초기화하지 않는다.
- camera/non-camera 일반 제한식과 독립 실제 출력 feedback의 차이를 임의 통일하지 않는다. 삭제된 CanfdStopRetry 설정을 부활시키지 않는다.
- 준비 횟수는 입력에 따라 달라진다. 기존 ECU held 표시가 있으면 첫 출력0 뒤 음수로 수렴할 수 있으므로, 첫 패킷을 무조건 -0.5로 강제하는 새 정책을 만들지 않는다.

## 검증·재현·완료 기준

1. 실제 parser→VCruise→card→CI.apply→controller→DBC 연결로 두 CANFD 경로의 정지·전환·unavailable·취소·페달을 검사한다. 화면/StopReq/단위 mock만으로 실제 정차를 판정하지 않는다.
2. 패들의 정상 ON 긴/짧은 홀수·짝수 송신과 production controlsd/TCS 내부 취소, raw CANCEL 동일 배치, OFF gap 불변·재ON 무재생·철회/재무장·직접 gap/비동기 저장을 검사한다.
3. DM0/1/2와 on/offroad·DriverView·AlwaysOnDM·ClusterHud, frame 지연/없음/복귀·프로세스 종료·ignition에서 기준 대비 전체 연결 동작을 대조한다. 새 DM 활성 참조와 미등록 키 접근은 없어야 한다.
4. VEgo1/2/5/9/10/50/100·설명/문서·branch 선택·blink·퇴역 프로필/자동화 중지와 관련 Python/JSON/웹 검사를 수행한다.
5. 고정 원본에 동봉된 소유 자산/hunk/삭제 manifest를 처음 적용해 최종 AST/JSON/텍스트/삭제/generated와 같고, 두 번째 적용은 불변이어야 한다. 대상 Git에 과거 FMS 객체가 있다는 가정 없이 재현한다. 형태 불일치는 fail-fast한다.
6. 최종 Astra 검토는 실제 변경과 실행 근거를 함께 평가한다. Linux/native/IPC·ECU·실차 미실행을 PASS로 표현하지 않으며, 환경 오류와 제품 실패를 구분한다. 알려진 HIGH/CRITICAL 계약 실패가 남으면 완료로 선언하지 않는다.

이 파일은 유지관리 방향과 완료 조건이다. 실제 구현·검증 결과와 미실행 범위는 해당 통합 기록에 따르며, 문서 존재 자체는 PASS 또는 게시 승인이 아니다.
