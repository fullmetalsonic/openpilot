# PaddleMode 4 WIP 통합 인수인계

## 상태와 사용법

- 통합 대상: `fms-carrot-wip`의 `49eba283`에 패들 브랜치 `ba34ad3c`를 일반 merge로 통합했다. 주행 기반 업스트림은 `43203371004e035bdb70a00a8dad29a4b657c6c3` 그대로다.
- 기능 단독 커밋은 `409799cd`이며, 통합 직후 전체 파일 트리는 기존 패들 HEAD와 동일했다. 이 문서의 상태만 최신화했다.
- 기존 패들 기능은 이미 커밋·게시됐고, 사용자는 재부팅 후 패들 차간거리 조절 동작을 확인했다. 이는 사용자 현장 보고이며 전체 실차 검증 완료를 뜻하지 않는다.
- 이번 통합은 로컬 소스·호스트 검증을 수행했다. 원격 게시 결과는 별도 운영 기록에서 확인하며, 이번 작업에서 기기 설치·설정 변경·재부팅·실차 시험은 수행하지 않았다.
- 웹캐럿 `패들시프트 모드`에서 4를 선택한 뒤 안전하게 주차한 상태에서 기기를 재시작한다.
- 4번에서 다른 모드로 나올 때도 재시작해야 한다. 기존 0~3 사이의 설정 반영 방식은 유지한다.
- 4번으로 작동 중에는 정상 크루즈 ON에서 새 패들을 한 번 당길 때 간격 선택이 한 단계 바뀐다. 대기·해제 중 순정 회생 단계 조작은 간격 설정을 바꾸지 않으며, 다시 ON해도 기존 간격을 유지한다.

| 현재 간격 | 오른쪽 | 왼쪽 |
| --- | --- | --- |
| 1 | 1 유지 | 2 |
| 2 | 1 | 3 |
| 3 | 2 | 4 |
| 4 | 3 | 4 유지 |

계속 당기거나 놓는 동작으로 추가 변경하지 않는다. 내부 personality는 표의 간격에서 1을 뺀 0~3이다.
실제 추종거리는 기존 TFollowGap1~4, 속도·감속 보정과 제어기의 제한을 따르므로 차량 간격이 즉시 바뀌는 것은 아니다.
모드4는 openpilot 종방향 제어 및 4단계 지원 구성에서만 패들 간격 조절을 수행한다.
CruiseGapLevels는 기존 간격 순환 버튼에 계속 적용하고 패들4는 항상 1~4단을 사용한다.

## 구조와 보존 계약

- `carstate.py`의 기존 LEFT/RIGHT CAN 신호와 press 이벤트를 그대로 사용한다.
- `cruise.py`에 gap-only 분기를 추가한다. 패들4로 cruise-ready, 자동감속, CarrotCruise나 크루즈 ON 요청을 만들지 않는다.
- CANCEL 이벤트가 같은 프레임에 있으면 gap 변경을 생략하며 기존 즉시 CANCEL latch를 보존한다.
- 패들 간격 입력은 유효한 carControl의 enabled/longActive와 현재 D단·CAN·MAIN 및 페달/override·주차 보호·취소 상태를 확인한 정상 ON에서만 접수한다. 내부 OEM 취소 요청은 운전자 CANCEL과 구분하며 현재 수신 배치의 CANCEL press/release도 차단한다. OFF·대기·CANCEL·독립 SoftHold 조작은 간격을 바꾸지 않으며, OFF부터 잡고 있던 패들은 ON 후 새 press가 있어야 변경한다.
- 두 패들 동시조작의 기존 왼쪽 우선 해석은 그대로다. 양쪽을 잡고 왼쪽만 놓으면 오른쪽 press가 생길 수 있다. 별도 동시조작 기능은 없다.
- `paddle_gap.py`의 단일 worker가 gap 증감·기존 순환·PCM 절대설정을 직렬화한다. 이전 디스크 쓰기 완료 후 다음 값을 읽어 빠른 연타 및 A→B→A 입력 누락을 막는다. 실시간 제어 루프에서는 동기 디스크 쓰기를 하지 않는다.
- worker 전용 Params를 사용하고, 타입/범위·쓰기 결과·readback을 검증한다. 실패하면 로그를 남기고 추가 요청을 거부하며, 다음 조작에 재시작 안내를 표시한다. 자동 무한 재시도는 없다.
- 정상 ON에서 접수된 요청은 저장 지연 때문에 OFF 후 완료될 수 있다. 큐를 취소하거나 설정을 복원하지 않으며 OFF 입력만 접수하지 않는다. 직접 간격 버튼과 웹 설정은 OFF에서도 기존대로 사용할 수 있다.
- 프로세스4에서 모든 간격 변경은 같은 큐를 사용한다. 프로세스0~3의 기존 비동기 간격 처리 방식은 보존한다.
- 모드4 진입/이탈은 제어 프로세스 시작 시 결정한다. 공유 `_paddle_decel_active` 상태를 무조건 지우지 않는다. 기존 CarController의 표시용 모드 읽기는 그대로여서 0↔4 전환 대기 중 계기판 배경만 선반영될 수 있다.
- 선택값은 기존 selfdrived personality → 추종거리 계산 및 HUD의 간격 표시 → CAN DISTANCE_SETTING 경로를 따른다.
- 초기 2026-09-26 구현에서는 종방향 제어기, CAN 생성 코드, safety, SoftHold, 방향지시등, Change Branch 및 자동화 설정을 변경하지 않았다. 2026-10-03 후보는 정상 ACC 모드4의 SCC2 패들 predicate와 내부 상태 전달만 제한적으로 바꾸며 기존 가속·정차 계산과 나머지 정책은 보존한다.
- 순정 회생제동 입력이나 경고를 억제하지 않는다. 순정 회생제동이 동시에 반응할 가능성이 남으며, 무경고나 연속 가감속을 보장하지 않는다.
- MQ4 HEV의 CANFD camera-SCC 정상 ACC에서 적용 모드4의 새 유효 당김에만 SCC2 패들 차단의 제한 예외를 적용한다. CANCEL·페달·입력/제어 이상 시 즉시 철회하며, 이후 새 양쪽 release가 필요하다. 독립 SoftHold 및 CarrotCruise에는 예외를 적용하지 않는다. 이 제한 예외 밖의 조건에서는 간격 변화와 별개로 기존 출력 제한이 적용된다. 독립 SoftHold 중 패들을 당기는 실차 시험은 정차 유지에 영향을 줄 수 있으므로 별도 안전 조건이 필요하다.

## 웹

`carrot_settings.json`에서 범위를 0~4로 확장하고 한국어/영어/중국어 제목·설명을 갱신했다.
기존 default와 저장된 선택값은 유지한다. 별도 control override 없이 공통 select 규칙을 사용한다.
실제 공통 UI는 현재값 버튼을 누르면 0~4 선택창을 여는 방식이며, 페이지에 5버튼을 강제하지 않는다.

## 이번 통합 검증

- Python 패들·기존 크루즈/SoftHold·설정 schema·Hyundai jerk/stopping: 466 PASS, Linux IPC 상태기계 6개 미실행. Windows Params import 대역과 `--noconftest`를 사용한 호스트 검증이며 native 통합 검증이 아니다.
- Node 패들·공통 선택기·선택창·선택 그룹: 23 PASS. 사용자 문서 검사와 Python AST, JSON 및 diff whitespace: PASS.
- 전체 helper를 최종 트리에 재실행해 tracked 파일 변경 없음. 별도 원본 fixture에서 업스트림 `43203371` → 기존 SoftHold/blinker/stopping helper → 패들 `409799cd` 레이어를 재적용해 4개 제어 파일 AST 및 패들 11개 파일 바이트 일치를 확인했다. 두 번째 helper 실행도 바이트 불변이다.
- CarController/CAN builder/stopping/planner/safety/방향지시등/브랜치 목록·다운로드·자동화 파일은 기존 WIP와 동일하다. 좌우 blink HOLD는 0이다.
- 기존 390×844 웹 검증은 동일한 웹 소스의 아래 결과를 재사용했다. 이번에 브라우저·실기기 시험을 다시 실행하지 않았다.

## 기존 구현 검증 이력

아래는 기존 기능 구현 때의 결과다. 이번에 다시 실행한 범위와 구분한다.

- Python 표적/영향 범위: 466 PASS. 새 패들 시험 78개, 기존 크루즈/SoftHold, 설정 schema, Hyundai jerk/stopping 포함.
- Node 공통 선택기/설정 관련 시험: 28 PASS.
- Python AST, JSON, git diff whitespace: PASS.
- 기존 사용자 패치 스크립트 재실행: 제어파일 내용 불변 PASS.
- 호스트 CAN 연결: CRUISE_BUTTONS/GEAR 각각 좌우 DBC pack→parse → 원본 CarState 패들 블록 → 실제 cruise/worker → 원본 추종시간 선택 → 두 CANFD builder pack→parse를 확인했다.
- CarState 전체 update, selfdrived IPC, full planner 실행을 이 연결시험이 대신하지 않는다. CarState tail과 추종시간 선택 함수는 소스 AST에서 추출했고 Windows의 Params는 시험 대역이다.
- 브라우저: 실제 웹 정적 소스+로컬 모의 API로 390×844 화면의 4 선택·저장·새로고침 유지, 가로 넘침 없음 확인. 모의 API 데이터는 실제 기기 Params가 아니다. 초기 모의 서버의 manifest 주입 누락을 수정한 뒤 새 브라우저 세션 오류 0 확인.
- 독립 Astra 검토: 저장값 변환과 PCM 최대값 보완 후 확인 범위 내 잔여 HIGH/CRITICAL 없음.
- 미실행: Windows에서 msgq가 없는 기존 상태기계 통합 6개, 전체 Linux/native 빌드, 실제 기기 Params·ECU·주행.

호스트 Python 시험 파일:

```text
openpilot/selfdrive/car/tests/test_paddle_gap.py
openpilot/selfdrive/car/tests/test_carrot_cruise_buttons.py
openpilot/selfdrive/carrot/server/tests/test_settings_schema.py
opendbc_repo/opendbc/car/hyundai/tests/test_jerk.py
opendbc_repo/opendbc/car/hyundai/tests/test_stopping.py
```

## 한계와 다음 기기 검증

큐는 메모리에 있으므로 프로세스 종료 시 아직 저장되지 않은 요청은 유실될 수 있다. 과도한 입력으로 큐64개가 차면 요청을 거부한다.
웹과 패들을 정확히 동시에 조작하는 외부 쓰기는 기존 last-writer 특성을 가진다. readback 사이의 충돌은 안전하게 writer를 중단한다.
기존 웹 변경이 저장된 뒤의 패들 조작과 연타·기존 간격 버튼 혼용은 시험했다.

통합 원격 게시와 기기 설치는 별개다. 향후 기기 설치를 요청한 경우 현재 브랜치/모드/롱컨 구성을 확인하고, 안전한 조건에서 좌우 단수·경계값·취소 유지·기존 인터록을 확인한다.
실제 주행에서는 선택단수·추종시간·계기판/CAN 표시와 제어기 보정 이후의 거리 변화를 구분해 확인한다.

## 2026-10-03 정상 ACC 출력 연속성 후보

모드4의 실제 적용 모드와 제어 상태를 같은 card 프로세스에서 전달한다. 초기화·제어 연결 손실 중 apply가 생략돼도 veto 이력을 유지한다. 현재 parser의 검증을 통과한 수신 배치(counter 및 메시지에 설정된 checksum)와 100ms 유효기간을 사용하며, 초기 캐시 0이나 반복 캐시로 재무장하지 않는다. 해당 SCC2 predicate만 변경하며 가속·정차 계산, Panda/DBC/원본 회생 입력은 바꾸지 않는다.

호스트 parser/CI/controller/실 DBC 검사는 ECU 수용 또는 실차 회생 단계 보존을 증명하지 않는다. Linux/native IPC·기기·실차 검증은 별도다.
