# FMS 최신 원본 통합과 개인 기능 계약

## 2026-11-07 어시스턴트 없는 원본00a6815 로컬 후보

선택 원본·재구성 기준은 `00a6815b802f34e483f28a8f35510f80ba1ce949`, Git 부모/게시 WIP는 `35c35c23e79ac25bbc92cc571f5f977b41f97d48`다. 직전 검증8d67935 후보45개 미커밋 변경을 먼저 지문 대조·사본 보존한 뒤13커밋·76경로를 접합한다. 개인7개와 공통보호를 유지하고 새 최초 GPT/CarrotAssist860a6fe의17신규경로 및 assistant 도구를 포함하지 않는다. 현재 source/게시/실제설치 SHA를 구분하고 커밋·푸시는 하지 않는다.

- 개인7개는 독립SoftHold/양CANFD·패들4·운전자 개입HOLD해제·legacyDM전체·VEgoStopping 최소1/저장값·원본+포크 브랜치선택·폰경유PC로그다. CameraSCC OEM MAIN/운전자 의도 분리, MQ4 원본 MAIN 요청, CANCEL/패들 같은batch 보호와 기존 페달/주차/invalid·stale입력 차단은 공통보호로 유지한다.
- 신규 healthy-front→vision 전환은0.20초의 거리/횡위치/속도 일관성을 확인하는 원본 정책이다. 그동안 최신 실측 radar값을 유지하고 연속성이 깨지면 보류를 철회한다. 정상 ACC 앞차 우선의 유효lead·실제ON/longActive·finite 상대2m 조건을 새 radar-only/same-ID/추가출발 대기로 바꾸지 않는다. 가까운/정지 vision도 확인 지연될 수 있으며 현장 긴급제동 효과를 호스트/원본 로그 숫자로 보증하지 않는다.
- CANable/SLCAN·지원어댑터 first-ready 소유권·B1 healthlease·비동기 source 종료는 원본대로다. 모델bridge에서 IPC/출력은 즉시 철회하고 종료회수는 비동기로 처리하며 정리 전 재시작을 막는다. native CANFD 및 BLE MQ4의 새 토크가 같은 HOLD를 재무장하지 못하는 보호를 유지한다. 이 통합은 실제 USB/펌웨어 설치·장치 등록을 수행하지 않는다.
- Light speed2는 선택된 외장 모델/artifact와 별도 runtime 계약으로 포함하며 legacyDM 모델·프로세스·DriverView·이벤트·forceDecel·알림 정책을 새 DM으로 교체하지 않는다. 어시스턴트 없는 source pin은 이번 버전 선택 예외이며 개인 기능 수를 늘리지 않는다.
- 실제 서빙 tools.runtime은00a 소스/번들/hash와 맞추고, FMS logs.runtime/logs.styles/route-summary.worker는 이전 검증본을 유지한다. 원본 manifest의 tools 항목만 접합하며 소스 시험만으로 배포 로그전송을 완료라 하지 않는다. 이전 동일소스 브라우저 저장 근거를 재사용하고 새 도구 UI/manifest는 해당 변경 검사로 검증한다.

최종 검사·재구성/멱등성·동결지문과 native/로그재생/실차 미실행 범위는 프로젝트 운영로그00a절을 따른다. 과거8d/4b candidate와 이번 완료본을 혼동해 덮어쓰지 않는다. 안정판731adee·기기/Params·예약·PC수신기와 기존 로그목록 미해결 진단은 그대로다.

## 2026-11-05 최신 원본8d67935 로컬 후보

게시 Git 부모는 `35c35c23e79ac25bbc92cc571f5f977b41f97d48`, 소스·재구성 기준은 `8d6793589e585f2e4c5dc441789850dd05b90851`다. 직전4b155d9 이후12커밋·36경로를 원본대로 접합한다. 개인7개와 공통보호·원본 기능 분류는 유지하며 기존7개를 새로 분리하거나 늘리지 않는다. 실제 게시/설치 SHA는 별도이며 이번 로컬 수정은 커밋·푸시를 포함하지 않는다.

- 끼어들기14c5398은 이미 부모35c35c2에 포함돼 있다. 이번 차이에는 새 radar/끼어들기 코드가 없으며, 모델 orientationRate xyzStd는 기록·오프라인 비교용이다. 기존 곡선 감속·앞차 정차/재출발 정책은 유지한다.
- 선택 Light speed 외부 GPU 런타임의 USB ring-wrap matcher와 warp 캐시 버전을 원본대로 가져온다. 정확한 pinned runtime에서의 positive matcher/native CPU·실제 USB/GPU 검증과 호스트의 제외/범위 검사 결과를 구분한다. 깜박이 정상완료 생산자와 schema field35는 그대로다.
- 레이 EV 외장 Comma Pedal은 원본 최종 TX 배율·Ray 한도만 적용하고 neutral/input driver threshold·명시 opt-in·차종/배선·안전 조건을 보존한다. 중간 변경과 revert를 별도 패치로 누적하지 않는다. MQ4 패들 모드4 기능과 혼동하지 않는다.
- 임시 Mapbox 검색 목적지는 RAM 우선이며 도착·manager 시작·offroad 전환에 수명을 끝낸다. CLI의 저장 목적지 교체와 늦은 응답 보호는 원본대로다. manager의 추가2줄은 새 RAM 키만 다루며 legacy DM·VEgo 저장값·최초 개인프로필 퇴역·사용자 설정을 보존한다.
- PC로그 소스·서버·서빙 로그 JS/CSS/manifest는 부모와 동일하다. 변경된 navigation_map.js는 별도 navigation.html이 직접 제공하는 페이지이며 generated manifest 입력이 아니다. 과거 로그 번들 누락을 반복하지 않되 무관한 로그 번들을 재빌드하지 않는다. 오늘 주행 로그 목록 누락 신고는 별도의 미해결 기기 진단이다.

최종 실행 검사·재구성/멱등성·새 지문은 프로젝트 운영로그8d67935절을 따른다. 아래4b 및 이전 설명은 당시 이력이며, SCC MAIN 상태 분리와 공통 CANCEL/패들 동시입력 수리를 최신 원본 재구성에서도 유지한다.

## 2026-11-04 SCC MAIN 상태 분리 — 원본4b155d9 로컬 후보

게시 Git 부모는 `3e02473cbcbd1a270eee5476261c3a220b0a0dce`, 소스·helper 재구성 기준은 `4b155d9e385195409e3928c138e457985a9ce06f`다. 원본의 바로 다음 SCC 수정1커밋을 접합하며 개인 기능7개·공통보호·원본 앞차 정차/재출발과 실제 PC 로그 배포 번들을 유지한다. 커밋·푸시·기기·설정·안정판·예약 변경은 하지 않는다.

- 계기판용 자동 순정 MAIN은 원본 조건대로 MQ4에도 요청한다. 기존 MQ4 OFF/대기 송신 차단 예외를 제거하고 CameraSCC+OP-long의 OEM MAIN 상태를 운전자 main_enabled에 복사하지 않는 원본 수신 분리로 대체한다. MAIN 요청은 SET/RES/ENABLE을 의미하지 않는다.
- AutoEngage0/1/2 초기화와 실제 MAIN/RES/SET·LFA 조작을 보존한다. OEM MAIN 토글만으로 cruiseState.available·latEnabled·activateCruise를 만들지 않는다. 운전자 CANCEL 뒤 OEM MAIN 응답이나 지연 입력으로 자동 ON/RES하지 않으며, 독립 SoftHold의 실제 StopReq와 페달/주차/무효 인터록 및 정상 ACC 출력은 기존 계약대로다.
- 모드4는 실제 ON/longActive에서만 gap을 바꾸고 OFF/대기/CANCEL/독립hold에서는 회생용 패들로 gap을 저장하지 않는다. 자동 MAIN의 CAM·Panda TX 반환 버스를 실제 ECAN 운전자 버튼과 구분한다. 현장 ECU의 별도 재방송·회생 단계·계기판 표시와 실제 제동은 호스트 검사와 구분한다.
- 공통 CANCEL 우선: 같은 CAN batch 또는 CANCEL 추적 중 패들 press가 버튼 count/선택을 덮어쓰지 않도록 한다. 실제 CANCEL→패들 순서와 역순, release와 겹친 입력도 보호하며 CancelButtonMode0의 짧게 조향 유지/길게 해제와 mode1의 짧게·길게 해제를 보존한다. 이는 기존3e02473에서도 재현된 조향 해제 누락을 고치는 좁은 수리이며 SoftHold·자동 재ON·패들 단독0~4 정책을 새로 정의하지 않는다.
- 원본9/368검사 기록은 원본 작성자의 환경 결과이며 FMS PASS가 아니다. FMS 실행 범위·최초 재구성·멱등성·한계는 프로젝트 운영로그의4b155d9절을 따른다. 주행 로그 목록 누락 신고는 별도 미해결 진단이며 이번 SCC 수정으로 해결됐다고 표현하지 않는다.

아래 MQ4 자동 MAIN 제외와14c/ed7 부모·검증 표시는 당시 이력이다. 이후 갱신은 이 상태 분리를 보존하고 이전 차종 차단 예외를 자동 복원하지 않는다.

## 2026-11-04 원본14c5398 기준

소스·재구성 기준은 `14c5398fca00d298c2791ecf43f46b3e3fd6cc38`이고 Git 부모는 게시 `eddd0cbe4fc6be91e3cb19562e5b3575157ada98`다. 원본 ed7fe4a 이후 13커밋·27경로를 통합하고 아래 7개 개인 기능과 공통 보호를 유지한다. 로컬 통합과 게시를 구분한다.

- B1 BLE 장치 등록·펌웨어 관리와 UI를 원본대로 포함한다. 발견 가능한 신규 장치의 자동 등록과 등록 후 관리가 있으므로 미사용 장비라는 이유로 코드 포함을 제외하지 않는다. 실제 등록·업데이트·차량 송신의 성공은 로컬 시험과 다르다.
- MQ4 HEV의 BLE B1 깜빡이 유지 요청에도 기존 FMS 운전자 개입 HOLD 판정을 적용한다. BLE 소스는 20Hz로 최신 carState를 읽으므로 native 100Hz가 관찰한 짧은 토크 펄스를 놓칠 수 있다. 이 시간적 한계와 실제 보드 반응은 실차 검증 범위다. 차선변경 중 새 토크에 유지 요청만 해제하고 같은 요청의 재유지를 막으며 새 실제 레버로 재무장한다. 물리 일회 입력과 원본 회전·조향 FSM은 기존 계약을 따른다.
- 원본14c5398의 근거리 끼어들기 판정은 모델 앞차가 바뀌기 전의 전방·코너 레이더 독립 이동 조건을 보강하며 종방향 입력에도 사용된다. 평행 이동·경로 회전·새 트랙 오인 차단과 앞차 정차·재출발 및 개인 SoftHold/패들 보호를 함께 검증한다.
- PC 로그 소스·배포 로그 JS/CSS와 연결은 게시 eddd0cb와 같은지 대조한다. 새 도구 UI 번들을 포함한 manifest 전체 해시와 실제 배포 번들 회귀 검사를 확인한다. 동일한 로그 번들의 브라우저 저장 증거는 해시와 의존성이 같을 때 재사용한다.

아래 ed7fe4a와 이전 승인·검증은 그 날짜의 이력이다.

## 2026-11-03 원본 ed7fe4a 로컬 통합

원본 소스·검토 끝·재구성 기준은 `ed7fe4a00ec0199c407f1a1d20f5338d5e720097`이며 Git 기준은 기존 게시 WIP `462940e9e5efe8a3a93fb7bad2a0d7fdcfcf7c32`다. 아래 개인 기능 7개와 공통 보호를 유지한다. 이 후보의 커밋·푸시는 별도 승인 범위다. 기존 WIP와 동일한 트리의 안정판 승격은 별개이며, 실차 결과와 기기 설치 SHA는 운영 로그에서 구분한다.

- 원본 내비 안내 회전·방향지시등과 우측 차선변경 준비 거리, 저속 곡률, 기본 OFF인 레이 EV용 외장 Comma Pedal 지원을 가져온다. 거리로 바뀐 기존 키의 백업 복원에서는 이전 BOOL의 false를 0, true를 250으로 처리한다.
- 계기판용 순정 MAIN 요청은 원본 송신 경로를 사용하되 쏘렌토 MQ4 HEV에서 새 OFF·대기 요청을 제외한다. 순정 MAIN 응답이 당근 대기 조건을 열 수 있고, 해당 차량의 회생제동·Panda 0x1AA 대기 프레임 및 표시 개선은 실차 미확인이다. 기존 활성 크루즈 요청과 기존 소프트홀드·패들 보호를 보존한다.
- 검증은 독립 SoftHold와 두 CANFD 정차 경로, 정상 ACC 앞차 재출발, 모드4 차간거리와 CANCEL, 방향지시등, legacy DM, VEgoStopping, 브랜치 선택 및 PC 로그 전송 계약을 기준으로 한다. 원본 문서의 원본 환경 시험 결과는 FMS 결과와 구분한다.

## 2026-11-01 현재 기준: 원본4a555c7 우선 + 명시 개인 예외

이번 사용자는 '최대한 원본을 따르고 개인 브랜치가 너무 독립적이지 않도록' 방향을 확정했다. **선택 원본을 기본 코드로 삼고 개인 기능에 필요한 예외만 재접합한다.** 과거 FMS고정·소수 선별·SPAS/자동등/Tesla/GPS 일괄 제외와0389e7a 재구성 고정은 현재 기준이 아니다. 이 절이 아래 역사 기록보다 우선한다.

| 구분 | 정확한 기준 |
|---|---|
| 원본 소스·검토 끝·재구성 baseline | `4a555c75b81c4ba80bfbf2260007a6baa645fcac` |
| 이번 통합 준비의 Git parent | `f283533d3243416a83aef89654d8c1b810f4840c`; 원본소스4a555c7/최종게시SHA/실제기기설치는각각구분 |
| 개인 시작 후보 | 게시 f283533의441 통합·CANCEL입력기원/정상완료stamp 수리까지 포함; 원본441..4a2커밋만 추가 |
| DM 기능 기준 | 현재 FMS legacy `DisableDM=0/1/2` 전체 연결; ef586a4 이후 새DM 정책 비채택 |
| 이번 후속 승인 | 발견2건수리·검증후 fms-carrot-wip 커밋/일반FF푸시. 안정판/DDM·기기/Params·예약변경금지 |

### 기본으로 따를 원본과 예외 판단

최신 일반 수정·NAV/GPS·CoG 연결·모델 카탈로그(N16 원본 기본 정책 포함)·Jetson/GPU·UI·원본 자동등/SPAS/Tesla 선택 기능의 코드와 의존 연결을 가져온다. 기존 저장된 실제 기기 설정을 Codex가 쓰거나 모델을 설치/선택한 것은 아니다. 원본의 기본OFF/차종/장비 게이트는 유지한다. 장비 미사용이나 실험이라는 이유만으로 기능 전체를 빼지 않는다. 새 제외는 구체적 개인 계약 충돌·결함·실패와 정확한 경로/함수/이유를 남긴다. 같은 파일의 비DM 최신 변경은 유지하고, 원본이 동일 기능을 충족하면 검증 후 개인 중복을 흡수한다. 원본 자료의 DM 전용 설계 문서는 upstream 참고 이력이며 FMS 실행 정책이 아니다. 현재 사용자용DM 설명과 아래 계약이 우선한다.

### 기능 분류와 유지 기준 — 개인 7개·공통 보호·원본 기능

**판정 기준:** '개인 패치'는 우리가 처음 개발한 모든 기능이라는 뜻이 아니다. **선택한 최신 원본과 다르게 유지하기로 사용자가 정한 기능·버전 예외**를 뜻한다. 원본과 같은 기능은 '원본 기능 보존', 여러 기능의 운전자 우선/유효성 조건은 '공통 보호'로 분리한다. 함수·파일·시험·보조 코드를 각각 별도 개인 기능으로 세지 않는다. 다음은 **원하는 동작의 정의**이며 현재 구현 전체가 통과했다는 선언이 아니다.

| 개인 기능·유지 예외 | 반드시 충족할 정의 | 범위를 넓혀 해석하지 않는 기준 |
|---|---|---|
| **1. 독립 SoftHold와 CANFD 정차 호환** | 비PCM 크루즈 OFF/대기/CANCEL에서도 실제 CAN 정차를 유지. hold 자체가 activateCruise/buttonEnable/RES/자동ON을 만들지 않음. CANCEL 후 가속으로 hold를 해제해도 자동ON 금지. 양 CANFD builder·독립 hold 음수 준비/유지·일반ACC 조절형 정지/직전 실제출력 feedback 연속성과 기존 인터록 보존. | 정상ACC 정차/재출발 전체를 우리가 만든 기능으로 세지 않는다. 기존 원본 정지거리 설정을 새로 고정하지 않는다. 원본 no-lead HUD2는 일반ACC 원본조건만 따르고 독립hold의기존bit출력은유지한다. |
| **2. 패들 모드4 차간거리/회생 분리** | 실제ON/longActive에서 새press당 오른쪽한단계감소(최소1), 왼쪽증가(최대4). 지속당김반복없음. OFF/대기/CANCEL/독립hold의회생조작은gap저장불변·재ON/release시재생없음. MQ4 HEV camera-SCC 정상모드4의정상당김은SCC2요청연속성유지. | 모드0~3은원본기능. 정상ON접수저장이OFF뒤완료되는것은허용; 큐전체삭제/예전gap복원으로직접간격버튼·웹설정을잃지않음. 범위밖차량/모드·페달/주차·invalid 인터록은우회하지않는다. |
| **3. 차선변경 중 운전자 개입 시 HOLD만 해제** | 최신원본레버/FSM/대기·예약·회전동작을사용. **진행중인차선변경**에서새steeringPressed개입을감지하면유지요청만해제·같은요청재유지차단. 시작확인토크제외, 새유효실제stalk조작은재무장. 모델/외부HOLD가차단을우회하지않음. | 차선변경이완전히끝난뒤일반조향을과거변경개입으로취급하지않음. 조향FSM/queue를새로취소하지않고원본반대토크취소는유지. 단순손접촉/모터토크를감지신호로바꾸지않음. 상시HOLD0·순정램프강제소등을뜻하지않음. |
| **4. 기존 DM 전체 버전 유지** | legacyDisableDM0/1/2와모델/daemon·영상/DriverView·프로세스/health·events/forceDecel·UI/sound/CAN·replay/GPU연결유지. ef586a4이후새DM/touch/3CANCELsession/정차pause/새설정으로이행하지않음. | 기존DM자체를우리신규개발로세지않는다. 사용자가선택한옛DM유지는개인버전예외다. 새DM을단순OFF로남기는것과다르며inactive동일ordinal/type예약은실행기능이아니다. |
| **5. VEgoStopping 최소1·저장값 보존** | min1/max100/default50/step5. manager시작/planner·modeld주행중10하한강제/비동기재저장없음. 실제읽기는get_float*0.01, 저장1/2/5/9등보존. | 사용자값을1로강제하지않는다. StoppingAccel/shouldStop/기존정지거리설정은이요청만으로변경하지않는다. |
| **6. 원본＋포크 브랜치 선택** | 원본ajouatom과fullmetalsonic목록을함께표시, 선택branch만안전하게fetch/전환. 목록조회는대량객체download없이수행, origin/현재branch/사용자설정을목록준비때바꾸지않음. | 잘못설정된예약remote를자동덮어쓰지않음. 목록문제로기기git clean/reset·저장소삭제하지않음. 별도명시sync/checkout동작과목록조회는구분. |
| **7. 폰을 경유한 내 PC 로그 전송** | 폰웹캐럿→기존Tailscale→집PC privateHTTP. 주행일KST·콤마별저장,무결성·lease/auth·중단재개/영수증보존. 실제서빙generatedJS/CSS/manifest포함. | 콤마자체Tailscale/새공개서버/Funnel/Pi/새인증을필수로추가하지않음. 브랜치코드포함과실제기기·폰·PC연결시험은별개. |

**공통 보호 조건 — 8번째 개인 기능으로 추가하지 않는다.**

- 실제운전자CANCEL은즉시우선한다. CANCEL전의패들·리모컨/제스처대기입력이뒤늦게새명령처럼처리돼크루즈를다시켜면안된다. 새실제조작의재무장은허용하되과거pending클릭·더블판정·long/repeat를새조작으로위장하지않는다. 내부OEM취소와raw운전자CANCEL은구분한다.
- 브레이크·가속override·주차브레이크/AutoHold·gear·입력무효/제어OFF의기존보호를우회하지않는다. 이를'브레이크해제후자동ON영구금지'로확대하지않고기존사용자AutoCruise설정정책을유지한다. CruiseOnDist0은거리자동ON만끄며전체자동ON차단을뜻하지않는다.
- 같은입력지속/invalid회복/재ON으로과거동작을재생하지않는다. 정상ON에접수된비동기저장/직접웹·버튼변경은잃지않는다. 저장값·origin·백업·기존미커밋작업을보존하고검증을위해assert/skip을약화하지않는다.

**원본 기능 보존·회귀 검증 — 개인 기능으로 세지 않는다.**

- **앞차 추종 정차·재출발 자체는원본기능이다.** 우리SoftHold/CANCEL/신호보조구조가원본의정상재출발을가리지않도록한호환코드가있을뿐이다. 현재유효제어/유한양수lead거리·필터모델stop과차<2m/정확2m제외·red/native기억·유효성철회·최종veto를연결해보존한다. 같은ID/radar-only/추가움직임대기/가짜green·ENABLE·RES를추가하지않는다. 원본만으로동등한동작·보호가충족되면검증후중복접합을흡수할대상이다.
- 최신일반차선변경·레버재입력/대기·다음1회예약/회전분류는원본이다. 개인기능3은운전자개입의HOLD경계만예외다. 패들0~3·일반크루즈·기존정지거리설정·일반조향과NAV/GPS/CoG·모델카탈로그/기본정책·선택SPAS/Tesla등도개인기능으로포장하지않는다. 차종/장비기본게이트와저장설정호환을원본대로따르되명시개인예외를침범하지않아야한다.
- source파일이나함수개수와개인기능개수는같지않다. `_lead_follow_eligible`/effective guard/owned-stay/최종planner guard는원본재출발을우리구조에서유지하는호환·보호경계이며별도신규재출발기능이아니다.

**퇴역·운영 방침 — 제품 개인기능과 분리한다.** 개인205최초설정/profile/marker는폐기했고백업복원을사용한다. 예약중지·안정판수동승격·DDM보관·기기/Params변경별도승인은운영방침이다. 옛navdQt/구branch백업workflow퇴역은최신원본정리이며새개인기능이아니다. 원본소스4a555c7/Git부모f283533/최종게시·실제설치SHA를구분한다.

**2026-11-01 후속수리 — 이검증본의WIP게시승인:** 사용자가 "수리후커밋푸쉬"를명시했다. 두반례를수리전FAIL로보존한뒤다음처럼원인수리했다. 최초의미수리발견·직전정의동결은과거증거로보존하고, 최신수리자산/검증/게시결과를따른다.

- **공통CANCEL보호:** Bluetooth제스처의실제시작시각`started`를발행`time`과분리했다. single대기flush·double·long/repeat·fastkey·touch의원래입력기원을reader취소cutoff와비교해CANCEL전입력이뒤늦게새명령으로재ON하지못한다. 발행time기반기존0.4초TTL/순서·blinkinput_time/attention/학습은유지한다. 취소전첫press가속한straddle-double은같은과거gesture로차단하고,완전히새single/double/long은정상재무장한다. 실제emit→writer/reader→MQ4parser/VCruise/상태기계의이전FAIL·수리후PASS,관련고유253PASS/기존fcntl모듈1SKIP다.
- **개인3완료경계:** 정상완료/취소가같은off/pre상태로보여단순Finishing→off초기화안은취소후늦은토크보호를약화하므로폐기했다. DH원본정상Finishing완료분기를관찰해최종oppositeTorque취소없고CAN유효할때만마지막정상완료monotonicstamp를생산한다. modeld→`ModelDataV2.MetaData.laneChangeCompletedMonoTime @35 :UInt64`로지속전달,이전0~34는변경하지않는다. 새정상완료stamp에만활동중토크감지구간을퇴역하고blocked는fresh실제stalk전까지유지한다. 0/stale/역행·invalid회복·model유실·취소는정상완료재무장으로쓰지않는다. 정상완료stamp동일주기와apply공백후다음Starting의확인토크는완료우선으로분류하되취소는stamp없음으로지연debounce보호를유지한다. 실제MQ4 DH/메타직렬화/parser/controller/DBC와관련고유155PASS,원본FSM·queue·조향판단/ACC·페달은불변이다.

수리생산코드는Bluetooth model/daemon 및BlinkerHold/정상완료관찰/메타전달에한정한다. SoftHold/두ACCbuilder·실제feedback·패들/크루즈/DM·VEgo·브랜치/PC로그는별도보존한다. 검사원문은외부프로젝트출력의`repair_bluetooth_result.json`/`repair_blinker_result.json`,최종독립검토/선택441최초재구성·멱등/전체동결·커밋tree일치를같이본다. 실제BT/IPC·ECU/램프·실차PASS가아니다. 이번게시승인은미래모든업데이트/안정판승격/기기설정승인이아니다.

### 2026-11-01 원본4a555c7 증분 갱신

원본6054829/4a555c7의 수동 경계접근 예측과 소유된 깜박이 반향 판별·초기램프/완료ACK 연결을 원본대로 가져온다. 기존 지원차종/기본OFF·운전자취소·자동조향 분리와 개인7개/공통보호는 유지한다. DH의 원본 신규HOLD_PATH/feedback만 접합하며 정상완료stamp35의 기존5줄은 그대로다. 원본AGENTS의 운영승인·원본rlog/CI 결과를 우리승인·실차PASS로 취급하지 않는다. 관련383PASS/실패0·독립Sol검토GO이고 helper/manifest를4a로 함께 올린 최초재구성·멱등·최종지문/게시대조가 완료조건이다. 직전Bluetooth253과 DM/SoftHold/패들/앞차우선/PC로그 등 미변경 근거는 소스·의존동일성을 확인해 재사용하고 검사수를 중복합산하지 않는다. 이번 사용자 WIP갱신 요청만 해당하며 안정판/DDM·예약·기기/Params는 불변이다. 외부 output/WIP_DELTA_4A555C7_2026-11-01의 실제결과와 최신운영로그를 본다. nativeIPC/ECU/실제램프·실차 미실행은 유지한다.

### 다음 갱신 절차와 검증 근거

1. 실제FMS HEAD/원격/미커밋후보·동결을먼저인수한다. 다음선택원본SHA를명시하고원본과현재개인예외차이를다시검토한다. 기존후보를reset/clean/stash/force로버리지않는다.
2. 최신원본의공통파일을기본으로개인예외만재접합하고, helper/manifest의기준도선택원본으로함께올린다. 옛원본파일복원패치를계속쌓지않는다. Git부모·원본sourcebaseline·최종게시/설치SHA를분리기록한다.
3. 관련SoftHold/CANCEL/두CANFD출력·패들ON/OFF/재무장·앞차우선/정지거리·깜박이실제parser→직렬화→builder·DM0/1/2연결·VEgo저장값·웹/로그를표적검증한다. helper최초/재적용멱등과선택원본의이력없는최초재구성·전체경로/제어AST일치를확인한다. 검사실패/미실행과환경차단을구분하고assert/skip을약화하지않는다.
4. 이번소유별결과는 `output/WIP_BASE_441D71E_2026-11-01/{can,controls,dm,runtime,web}_result.json`, 최종전체동결·재구성·독립Sol검토·완료결과와최신운영로그를같이확인한다. 호스트pack/parse/fixture는nativeIPC/ECU·회생·실제소등·실차PASS가아니다. 설치/주행확인은별도다.

---

## 이전 계약·검증 이력 (위 절로 대체된 방향/상태는 현행으로 재사용하지 않는다)


## 2026-11-01 깜박이·레버 로컬 후속 계약

게시 WIP/HEAD는 `e0f14004a52061895eae47d230327bf03f05065c`다. 사용자는 원본 `00d791247114527317b4e68ca296a6ddd1b0316d`의 레버·차선변경 동작을 선별 이식하고 진행 중 운전자 조작 때 깜박이 유지 요청만 해제하도록 로컬 수정·검증을 승인했다. 커밋·푸시·기기 설치/Params·안정판/DDM·예약 변경은 승인하지 않았다. 아래 날짜별 HOLD0/레버 보류 기록은 당시 정책이며 현재 WIP에는 이 후속 계약이 우선한다. 안정판의 기존 HOLD0 계약은 변경하지 않는다.

- 실제 CANFD 레버 누름 횟수·원터치/고정 위치·원시 램프를 읽는다. 같은 쪽을 다시 누른 요청, 차선 교차 뒤 다음 한 번 예약, 대기 만료·새 요청 재무장, 원본 토크 필수/BSD/차선/반대 토크 취소를 함께 보존한다. `BlinkerLatchedTurn=0`, `LaneChangeLeverWait=False`가 초기값이며 사용자 저장 설정을 쓰거나 기존 설정을 초기화하지 않는다.
- 원본의 현재 차선 교차 기반 HOLD 및 대기·예약 HOLD를 연결한다. 이미 시작된 차선변경에서 기존 `steeringPressed`가 새 운전자 개입을 감지하면 다음 출력부터 HOLD만 0으로 한다. 시작 시 이미 누른 확인용 토크는 release 뒤 새 개입과 구분한다. 같은 요청의 모델/예약 HOLD로 되살리지 않으며 새 실제 레버 조작만 재무장한다. 추가 조향 취소·가감속/페달 정책이나 queue 삭제를 만들지 않는다. 반대 토크에 대한 원래 FSM 취소는 별개로 유지한다.
- 스키마는 기존 `CarState@100..103` UInt8 및 `ModelMeta@20/21`의 동일 번호·타입을 복원한다. DM/touch 예약이나 다른 새 원본 필드는 활성화하지 않는다. 단순 손 접촉은 이번 판정 신호가 아니다.
- HOLD0은 콤마의 유지 요청 해제이며 순정 레버 고정·원터치 점멸의 실제 즉시 소등을 보장하지 않는다. ECU·램프 타이밍·실차 결과는 호스트 소스/DBC 시험과 분리한다.
- 새로운 SPAS/UDS 송신·USB2CAN/ATC 자동 점등·Tesla 신호 경로는 도입하지 않는다. 기존 Bluetooth·ATC 계산과 DM/SoftHold·양 CANFD stopping/실제 출력 feedback·앞차 우선·패들/CANCEL·VEgoStopping 최소1/저장값·원본+포크 선택·개인 최초값 퇴역·로그 전송·예약 중지를 보존한다. 최종 검사와 재구성 수치는 운영 인수인계의 이 날짜 항목을 따른다.

## 상태와 방향

**2026-10-23 현재 로컬 후속 작업:** 게시 기준은 `647f9c9`다. 사용자는 다른 차로 신호·적색 오인 가능성 때문에 정상 ON/활성 종방향 크루즈에서는 원본의 앞차 추종을 우선하도록 명시했다. 이전의 보조 적색 절대 우선/앞차 예외 보류는 이 요청으로 변경한다. 실제 사건 원인은 당시 로그가 없어 미확정이며, f76/647의 동일 동작만으로 원본과의 정책 차이를 배제하지 않는다.

- 예외는 현재 실제 제어·입력 자격이 있을 때만 허용한다. 앞차 status, 유한한 양수 dRel, 기존 필터 모델 정지거리와의 차이 `<2m`를 사용한다. 별도 radar-only/같은 ID episode/연속 움직임 대기 조건은 추가하지 않는다. 이 2m는 앞차까지 절대 거리가 아니다.
- 정상 ON/longActive·drive·유효 입력·사용자/차량 hold 없음·CANCEL/음수 요청/페달 override 없음이 필수다. 내부 OEM 취소 `carControl.cruiseControl.cancel`을 운전자 CANCEL로 오인하지 않는다. card의 음수 activateCruise와 raw buttonEvents, 기존 controls OFF 경로는 보존한다. planner에 별도의 과거 CANCEL latch를 새로 만들지 않는다.
- 유효 출력 결정만 hold/released/green-confirmed/red 주입을 억제한다. 내부 적색 기억·원시 신호 표시와 실제 CarrotMan stay/native 상태는 지우지 않는다. 같은 자격으로 FMS가 추가한 `owned_stay_stop`의 강제 정차만 양보한다. 앞차 소실/거리 이탈/입력 무효에는 남은 적색 veto가 즉시 복귀하며, 앞차 없음·독립 SoftHold/CANCEL·명시 stop·페달 보호는 그대로다.
- 과거 기록의 일시적 vision lead 약449ms 예외와 적색 중 약1.53m 이동, 모델 경계 반복 전환 가능성은 알려진 한계로 남긴다. 이 정책 선택은 차로별 신호 판별이나 잘못된 lead 식별을 해결한 것이 아니다. 기존 MPC/LongControl/CAN 출력 수식은 바꾸지 않는다.
- 단계는 로컬 수정·관련 검증이다. 커밋/푸시·기기/Params·안정판/DDM·예약은 변경하지 않는다. 아래 10/22·10/18의 미게시 상태와 앞차 예외 보류는 당시 기록이다. 최종 검사 결과와 미실행 범위는 운영 인수인계를 따른다.

로컬 검증: 변경 전 정상 ACC·적색·유효 앞차 입력은 기존 hold로 새 요구를 실패했고, 수정 후 같은 검사를 포함한 영향 묶음 1,233개 및 추가 native-first/auxiliary-first의 앞차 인계→동일 주기 소실/OFF 4개가 통과했다. 실제 백업 초기화·10/20/30/40주기 reader·주행/신호 모드·정지거리5.2m/보정0/VEgoStopping0.01·정지/출발 lead·CarrotMan stay를 Python MPC 장애물과 최종 veto까지 확인했다. 정지 lead의 장애물은 유지되며, recordingsolver나 진단 진행궤적을 실제 가속·실차 출발의 증거로 확대하지 않는다. 두 CANFD/SoftHold/CANCEL/패들 관련 검사도 포함하며, 생산 MPC/LongControl/CAN/DM 경로는 변경하지 않았다. native/acados/IPC·ECU·실차 검증은 미실행이다.

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
| 방향지시등 | 2026-11-01 WIP: 원본 레버/차선변경 HOLD를 선별 이식하고 진행 중 새 운전자 조작에는 유지 요청만 해제. 이전 상시 HOLD0은 안정판과 과거 이력에만 적용 |
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
