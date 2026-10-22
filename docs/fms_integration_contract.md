# FMS 선별 통합 운영 계약

## 상태와 방향

2026-10-12 사용자 결정: FMS는 원본의 동작과 같아지는 것을 목표로 하는 자동 동기화 브랜치가 아니다. **현재 FMS의 동작 계약을 유지하며 필요한 원본 변경을 선별 이식하는 포크**로 관리한다. 설정 몇 개를 바꾸는 수준의 패치만으로 해석하지 않는다.

최신 원본의 검토된 비DM 변경을 통합·검증하며, 해당 작업에서 사용자가 승인한 경우에만 WIP에 커밋·푸시한다. 기기 설치/설정 변경, 안정판/DDM 갱신과 예약 재개는 별도 명시 요청이 필요하다. 중요한 통합은 변경 위험·실패 이력·사용자 지정에 맞는 실제 Astra 강도로 코드와 근거를 최종 검토한다. 이번 2026-10-22 수리는 사용자 지정에 따라 실제 Astra Max가 설계·코딩·검증했고, 모든 작업 뒤 별도의 실제 Astra Max가 마지막 읽기전용 검토를 수행했다. 이 한정 승인을 모든 향후 패치의 Max 의무로 확대하지 않는다.

- FMS 동작/DM/개인 패치 기준: `dca1f33137b8f24dfd23e35bc2bc3d9fd21c5d69`.
- 원본 기반: `ef586a41637a00968e4521aeb99021fe609b5105`.
- 2026-10-17 당시 통합 출발 FMS: `12e39f8a93ed1e0c787008dba86e0344d4b099c7` (직전 검토 원본 `bd53684a4d2b5809ecd68f75986a76e548a61470`).
- 2026-10-17 당시 추가 검토 대상: `0389e7a59be576aefdc8ad390728a32656c1dbc2`.
- 안정판 유지: `609cecef51011d893e252cf61079ba6b90faabc4`.
- 원본 보관 DDM 유지: `ef586a41637a00968e4521aeb99021fe609b5105`.

Git 병합은 이력과 충돌을 추적하는 도구다. 원본의 모든 정책을 채택한다는 뜻이 아니다. 혼합 커밋/파일에서는 채택할 비DM 변경과 제외할 DM/정지 정책을 분리하며, 넓은 revert나 과거 공유 파일 덮어쓰기로 비DM 변경을 잃지 않는다. 향후 SHA가 달라지면 기존 계약 영향과 의존 변경부터 다시 검토한다.

## 2026-10-22 승인14개 — 로컬 적용·검증

**현재 인수 기준:** 게시 HEAD는 f76bb19이고 승인14개·Max 수리는 그 위의 미커밋 후보에 있다. 검토 끝은 7e371b9, helper baseline은 0389e7a로 별개다. 먼저 후보와 최신 동결 manifest를 대조·인수하고, 게시 HEAD로 reset/overwrite하거나 원본부터 재생성하지 않는다. 차이는 먼저 원인을 확인한다. 최종 제어 소스의 1,276 호스트 PASS·재구성·독립 Max GO는 로컬 검증이며 native/IPC·ECU·실차 성공 판정이 아니다. 커밋·푸시 및 안정판/DDM·기기/Params·예약 변경은 승인되지 않았다. 다른 날짜의 상태·승인은 당시 이력이다.

신호 정차의 지속 인수 조건: 앞차 소실과 OFF가 같은 주기도 이전-cycle lead 문맥을 cleanup과 모델 판정에 함께 보존하고, 이미 lead가 없던 다음 cycle에는 그 문맥을 재사용하지 않는다. 보조-only 정리·native 공동소유·명시 hold 인계와 함께 회귀 검증한다.

- 출발 FMS `f76bb193aa6f3063507857134fe37bb4a42bb9b2`, 검토 원본 `163c000e05641adea88e9221094d45c90c3e186d` 이후 `7e371b92fd877c2cd160c92b4cf6fc62fe6d8805`까지. 재구성 기준은 `0389e7a59be576aefdc8ad390728a32656c1dbc2` 그대로 유지한다.
- 승인14: `988b462`, `2cd1d79`, `fa55346`, `ba18691`, `4abe6ed`, `2c076bc`, `a28734c`, `10cd686`, `2ea4415`, `10d48e6`, `1b0bac6`, `c5a5876`, `e925a3c`, `19719f4`. 원본 전체 복사/새 DM/이전 보류 GPS/미도입 memory capture 수집기/13개 현장기록의 FMS 검증 주장 전환은 하지 않는다.
- S3는 사용자 미사용 확인에 따라 퇴역한다. S3 전용 Wi-Fi·페어링·펌웨어·설치·HTTP 경로/시험을 정리하며, 앞서 보존한 `2c479ea` Wi-Fi 대기 예외도 사용 경로와 함께 퇴역한다. 본체 USB·Jetson wrapper와 유선 eGPU 로딩·안정화는 유지한다. `a729633`/`e93d466`의 중간 S3 QP/큐 정책을 추가하지 않는다. 새 Jetson Wi-Fi HUD runtime은 원본 carrot-nav 전용이며 FMS WIP에 추가되지 않는다.
- 신호 정차 기억의 출발 퇴역은 5km/h 초과에서 수행하며, 녹색 확인/출발 허가를 만들지 않는다. FMS의 저속 적색 우선·앞차 우회 금지를 유지한다. 현재 실제 CarrotMan 입력 뒤 판단하고, 이미 보조가 소유한 정차 episode의 현재 유효 stay 요구를 보존한다. 이 소유권은 실제 guard 제어 자격 `enabled and drive and not suspended`가 유지될 때만 유효하다. 문맥은 주기당 한 번 읽어 기존 출발 정리 앞과 뒤 guard에 함께 사용한다. 자격 철회 때 **보조만 소유한 정지 상태**를 기존 정리 위치에서 같은 주기에 철회한다. 이미 native 모델 정차가 함께 성립했다면 현재 상태·거리·타이머를 지우지 않고 기존 native 상태기계에 맡긴다. 먼저 native였던 경우와 보조 획득 뒤 native가 진입한 경우를 모두 구분하며, 보조 red 덮어쓰기 전의 모델 결과와 기존 lead/조향/출발 타이머/모드 gate로 공동소유를 판단한다. 보조가 만든 e2eStopped를 모델 감속 오인식 억제의 우회 근거로 쓰지 않고, 기존 이전 lead 정보로 실제 lead 소실 시 native 진입도 보존한다. 새 bool은 같은 episode의 provenance일 뿐 정차/ACC/ON 권한이 아니며 거리·타이머 snapshot을 복원하지 않는다. 짧은 모델 unknown은 native 기억을 지우지 않지만 실제 상태 이탈·가속 페달·내비 출발·명시 hold 인계·episode 종료는 다음 정차에 소유권을 남기지 않는다. SoftHold와 별도 명시 거리 요구는 보호하고 현재 활성 stay 보호를 OFF까지 연장하지 않는다. 관측 invalid/소실은 자격 철회와 구분하며 브레이크 단독을 native 정차 취소로 확대하지 않는다. 검증에는 실제 runtime→planner→ACC MPC 장애물/입력과 blended 경계를 함께 포함한다.
- 최종 mode의 추가 ACC 유지 범위는 기존 start-hold 또는 페달 취소 처리 후 남은 내부 user-stop 거리 또는 현재 제어 자격이 있는 보조 소유 episode의 실제 stay다. 일반 stay 전체/SoftHold 전체/단순 e2eStopped로 확장하지 않는다. `user_stop_distance`는 현재 제품에 0 이상 값을 공급하는 생산자가 없는 내부 분기이며, 사용자 설정 StopDistanceCarrot/TrafficStopDistanceAdjust와 같은 기능이 아니다. 0/양수 직접 입력 시험을 활성 사용자 설정이나 실차 기능의 증거로 설명하지 않는다. 기존 거리 차감·MPC/LongControl/CANFD 수식·SoftHold 자체의 activateCruise/RES 금지는 바꾸지 않는다. 관련 설정의 정상 계산 동일성과 OFF/재활성 상태 전이 검증은 별도이며, 기존 자동 ON이 planner 상태를 읽으므로 전체 재개 타이밍 무영향을 소스 동일성만으로 단정하지 않는다.
- 카메라 BSD는 `OnnxBsdEnabled` 기본0으로 별도 선택한다. 기존 ShareData1 사용자도 새 스위치를 켜야 카메라 BSD가 동작한다. OFF는 모델 reference 해제·내부 결과/세대 삭제, 차선 인식 및 OEM BSD 유지다. 이미 발행된 결과는 다음 프레임 clear 또는 기존 1.5초 TTL까지 남을 수 있으며 전 경로 즉시0지연이라 표현하지 않는다. TCP7711 일반 차량·모델 방송은 제거하고 신선한 Tesla DAS_road만 유지한다.
- USB 전원 코드는 지원 노드·빈 포트/재검사 조건부다. USB_ICL0은 daemon 종료 시 복원하지 않고 재부팅까지 유지하며 포트 확인/쓰기가 원자적이지 않다. USB worker FIFO2는 지원 kernel/architecture/device·Jetson bulk·관측 DWC3/PID identity·16개/3초 lease·journal/복구실패 차단 조건부다. 이 로컬 작업에서는 실제 전류·스케줄러를 조작하지 않는다.
- 연결 대기 표시 유예는 실제 inference/transport deadline 완화가 아니다. CameraGapTrace는 진단이며 카메라/위치 오류를 해결했다는 뜻이 아니다. Mapbox float는 형식 보강이고 현재 WIP의 기존 오류 재현 수정은 아니다. 구 DM·정지/feedback·패들·CANCEL/Bluetooth·VEgoStopping·blink·branch 선택·초기 프로필 퇴역·자동화 중지 계약을 그대로 보존한다.
- 단계: 로컬 소스·비파괴 호스트 검증만 승인. 커밋/푸시·기기/Params·안정판/DDM·예약 변경은 수행하지 않는다. 실제검사/실차미실행과 기존 Tesla angle3 FAIL 한계를 구분한다.

## 2026-10-18 선별 수정 — 게시 전 당시 로컬 검증 이력

게시 기준 FMS `7eba4cb4c453f5bc9a2194c1c1e13ccfb5aea0e9`에서 시작한다. 검토 범위는 원본 `0389e7a59be576aefdc8ad390728a32656c1dbc2` 이후 `163c000e05641adea88e9221094d45c90c3e186d`까지이며, helper 재구성 기준은 `0389e7a59be576aefdc8ad390728a32656c1dbc2`로 유지한다. 원본 최신 전체를 채택했다는 뜻이 아니다.

- 채택: `163c000`의 종료 PID 캐시 정리·starttime을 통한 재사용 구분·daemon 자원 생성 전 `gc.freeze()`, 해당 회귀시험.
- 채택: `2c479ea`의 Wi-Fi HUD USB 안정화 대기 생략. 실제 eGPU 모델 로딩 대기와 유선 USB 안정화 대기는 유지한다.
- 보류: `2332775`/`7ce2644` GPS 변경. CAN 방향 미확정 정보가 기존 navd/HUD에서 확정 방향으로 쓰일 수 있어 소비자 보강 전에는 이식하지 않는다.
- 미채택: `4b0dc02` 수동 진단 수집기와 기타 원본 기록·운영 지침. 메모리 부족 전체 해결, HUD 끊김 해결, 장기/실차 검증으로 확대하지 않는다.

이 절의 당시 단계는 게시 전 로컬 적용·검증이었다. 이후 사용자의 별도 승인으로 f76bb19에 게시됐으며 현재 10/22 후보의 게시 승인은 아니다. 아래 기존 소유 계약과 실제 제어 소스/의존성은 그대로 보존한다. 변경 함수 표적 검사와 pristine 재구성·재적용 불변·기존 경로 동일성으로 검증하며, 변경 없는 제어 검증은 이전 근거를 재사용한다.

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

2026-10-17 CANCEL 보존 보강: 같은 수신 batch의 CANCEL press/release가 최종 buttonEvents에 남지 않아도 card가 전달한 raw 증거를 VCruise에서 소비한다. gas 준비 전에 취소를 latch하고 같은 batch의 RES/SET·이전 +/- release·원격 GO보다 우선한다. non-PCM은 기존 음수 취소 요청을 실제 buttonCancel·상태 해제·다음 제어 출력까지 연결한다. 미완료 버튼 decoder만 폐기하고 SoftHold 자체·기존 lateral CANCEL 정책·정상 ON gap 비동기 저장·새 명시적 재개는 보존한다. buttonEvents 합성, 정상 queue 전체 삭제, 새 PCM 정책과 CC.cancel 생산식 변경은 하지 않는다. 이 경계의 실제 검증 결과는 해당 통합 근거를 따르며 문서만으로 PASS를 선언하지 않는다.

같은 CANCEL은 블루투스 cruise reader에도 전달한다. 취소 시각을 20ms throttle보다 먼저 메모리에 기록하고 그 시각 이전·동일 시각에 생성된 명령은 늦게 게시되어도 소비 후 거절한다. 취소 뒤 새로 생성한 명시적 명령은 기존 TTL·중복 방지·읽기 주기를 따라 접수한다. lane reader와 일반 allowed=False 정책, writer 및 저장 큐를 바꾸지 않는다.

## DM 동결의 접합 경계

새 DM을 넣고 OFF로 쓰는 방식은 기존 DM 보존이 아니다. 새 Enabled/Mode/Vision 설정 이행, fallback/session OFF/CANCEL 3회/touch/preview/DM 경고 주입/전용 cadence·camera async 보호를 채택하지 않는다. 기존 DisableDM1/2에서는 DriverView도 DM model/daemon을 강제로 켜지 않는다. 현행 forceDecel은 DisableDM0에서 alert3 또는 softDisabling일 때 true이고, 1/2에서는 false다. 이를 모든 통신/프로세스 오류 무시로 확대하지 않는다.

최신 비DM wire 번호를 보존하기 위한 CarState @94와 OnroadEvent @126의 DEPRECATED 비활성 예약은 새 DM 실행과 구분한다. 생산자/소비자는 없어야 하며 비DM BSD/systemReady/Impact 번호를 당기지 않는다. 단일 최종 schema로 바인딩을 맞춘다. 신호 관찰기의 GPU 자동 선택은 구 DisableDM==0의 사용을 보수적으로 예약하도록 최소 접합하고, 기존 DM 자체를 변경하지 않는다.

## 정지 제어 보존의 접합 경계

`2608792b`의 zero-aReq/복구 FSM 삭제와 `82290515`의 첫 StopReq jerkUpper 변경은 정지 범위만 분리해 제외한다. 원본이 ECU에서 실패한다는 판정이 아니라, 현행 FMS 출력 계약을 유지하기 위한 선택이다.

후속 `0e4ee1bd`의 일반 ACC lower-band 선출력·접근 단계 lower=0.20·blocked lower 변경도 제외한다. `stopping.py`, `hyundaicanfd.py`와 현재 정지 시험을 직전 FMS 기준으로 유지하며 새 레이더/신호 기능은 별도 접점으로 통합한다.

- `stopping.py`의 현행 prepare/request/release/retry/fallback/held 코어와 a_ego/value/previous_value/independent_hold 의미를 유지한다.
- `hyundaicanfd.py`의 두 builder에서 원래 enabled 기준 일반/독립 분리와 인터록, 실제 aReq 반환/전달을 보존한다. gate 예외만 복구해서는 음수 준비/유지가 돌아오지 않는다.
- `carcontroller.py`의 stopping 생성자/호출/실제 출력 feedback과 패들 문맥을 최신 비DM 코드에 접합한다. 직전 출력을0으로 초기화하지 않는다.
- camera/non-camera 일반 제한식과 독립 실제 출력 feedback의 차이를 임의 통일하지 않는다. 삭제된 CanfdStopRetry 설정을 부활시키지 않는다.
- 준비 횟수는 입력에 따라 달라진다. 기존 ECU held 표시가 있으면 첫 출력0 뒤 음수로 수렴할 수 있으므로, 첫 패킷을 무조건 -0.5로 강제하는 새 정책을 만들지 않는다.

## 추가 비DM 기능의 경계

- 2026-10-17 후속 bd53684a→0389e7a5는 조사 기록과 중앙 신호 선택의 오프라인 도구만 추가한다. selector/replay를 observer·표시·출발 guard·moving preparation에 연결하지 않는다. 전색상 제거 비교기는 명시적 오프라인 실험일 뿐 채택한 차량 정책이 아니다. 원본의 개발 표본·멈춤 guard 대조·메모리 관찰을 FMS 차량의 정확도·누수 해결·장기 안정성 증거로 옮겨 적지 않는다. 이번 갱신의 기존 제어 검증은 생산 코드·의존 해시 불변을 확인해 재사용한다.
- 2026-10-17 새 레버 차선변경은 HOLD=1에 의존하고 설정0에서도 일부 큐/재진입 정책이 활성이라 보류한다. FMS는 기존 desire_helper와 HOLD0을 유지한다. 새 레버 helper/설정/메뉴/modeld publish는 넣지 않는다. car.capnp stalk@100..103 및 log ModelMeta@20/21은 같은 타입·번호의 DEPRECATED 비활성 호환 예약이며 생산자·소비자는 없다. 활성 레이더/신호 schema 필드는 별도로 유지한다.
- 원본 e944a8c5의 lead에 의한 보조 적색 veto 우회는 보류한다. 적색 hold가 먼저 유지되고 hold가 없을 때만 기존 native stop→lead 재출발 조건을 적용하는 FMS 순서를 보존한다. 원본의 구체적인 무페달 적색 이동 기록 때문에 제외하는 것이며, 실제 ENABLE/RES를 만들지 않는다는 사실만으로 정지 제한 해제의 위험을 판단하지 않는다. lead 상태에 따른 알림 억제 5405f5a4는 출력·hold·최종 shouldStop이 불변인 독립 기능으로만 채택한다. 새 거리/시간/영구 latch 우회는 추가하지 않는다.
- 원본 7dd3c11e의 정차 SoftHold 중 새 DM 평가 pause/disabled packet은 현행 DM 동결에 따라 채택하지 않는다. 비DM eGPU warm fallback·기동 복구·overlay 개선은 기존 DM/GPU 예약·DriverView·events/forceDecel 연결을 보존하여 접합한다.
- `SignalAssistMode`는 기본0이다. 0에서는 과거 production enable 파일도 제어를 켜지 못한다. 1은 표시·알림과 정차 적색 출발 제한, 2에서만 추가 감속 준비를 허용한다. 기존 파일을 새 설정으로 자동 이행하거나 설정을 임의로 켜지 않는다. 모드 변경은 캐시 주기를 거치며 저장 즉시 0ms 해제를 보장하지 않는다.
- 새 `e2eStopPrepare=6`을 VCruise의 기존 정지 신호 `[3,5]`에 추가하지 않는다. 신호 준비가 SoftHold/자동 크루즈 ON·RES 권한을 만들지 않으며 OFF/CANCEL·페달·lead·회전·모델 정지·무효/스테일 관측에서는 기존 제어 경계를 유지한다.
- Bosch passive 레이더는 허용된 비CANFD 차종·완전한 동일 버스 지문·EnableRadarTracks 조건에서만 선택한다. MQ4 CANFD는 제외되며 기존 공급자·20Hz 경로를 보존한다. 이 구성이 수신용이라는 설명과 특정 Bosch 구성에서 ECU disable/트랙 활성화 쓰기를 생략하는 변경을 함께 검증한다.
- Mountain Dew 재학습 모델과 신구 runtime API 호환은 주행 모델 변경이다. 기존 DM 동결과 구분하며 패키지 무결성/선택 실패 시 내부 모델 fallback을 보존한다. URL 접근 검사나 host fixture를 실제 eGPU 추론·실차 성능 검증으로 확대하지 않는다.

## 검증·재현·완료 기준

1. 실제 parser→VCruise→card→CI.apply→controller→DBC 연결로 두 CANFD 경로의 정지·전환·unavailable·취소·페달을 검사한다. 화면/StopReq/단위 mock만으로 실제 정차를 판정하지 않는다.
2. 패들의 정상 ON 긴/짧은 홀수·짝수 송신과 production controlsd/TCS 내부 취소, raw CANCEL 동일 배치, OFF gap 불변·재ON 무재생·철회/재무장·직접 gap/비동기 저장을 검사한다.
3. DM0/1/2와 on/offroad·DriverView·AlwaysOnDM·ClusterHud, frame 지연/없음/복귀·프로세스 종료·ignition에서 기준 대비 전체 연결 동작을 대조한다. 새 DM 활성 참조와 미등록 키 접근은 없어야 한다.
4. VEgo1/2/5/9/10/50/100·설명/문서·branch 선택·blink·퇴역 프로필/자동화 중지와 관련 Python/JSON/웹 검사를 수행한다.
5. 고정 원본에 동봉된 소유 자산/hunk/삭제 manifest를 처음 적용해 최종 AST/JSON/텍스트/삭제/generated와 같고, 두 번째 적용은 불변이어야 한다. 대상 Git에 과거 FMS 객체가 있다는 가정 없이 재현한다. 형태 불일치는 fail-fast한다.
6. 최종 Astra 검토는 실제 변경과 실행 근거를 함께 평가한다. Linux/native/IPC·ECU·실차 미실행을 PASS로 표현하지 않으며, 환경 오류와 제품 실패를 구분한다. 알려진 HIGH/CRITICAL 계약 실패가 남으면 완료로 선언하지 않는다.

이 파일은 유지관리 방향과 완료 조건이다. 실제 구현·검증 결과와 미실행 범위는 해당 통합 기록에 따르며, 문서 존재 자체는 PASS 또는 게시 승인이 아니다.
