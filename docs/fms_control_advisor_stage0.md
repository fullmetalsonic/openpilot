# FMS 제어 분석 — 단계 0 착수 기록

확인일: 2026-10-25. 상태: **기기 설정 저장·QR 인증·원복·분할 기록·독립 내보내기 경로까지 PC/WSL 연결 검증**. 아래 각 절은 당시의 확인 범위다. 최신 구현과 남은 작업은 마지막 절을 따른다.

최신 후속 기획: [오토튜너 경량화·실차 완성 계획](fms_control_advisor_completion_plan.md). 사용자 요청에 따라 첫 기기 연결 이후의 목표·측정·구현·주행 검증 순서를 간소화했다. 이 기획 추가는 제품 변경이나 기기 반영 승인이 아니다.

후속 승인: 실제 기기 확인 후 로컬 실행 배치 수정·계획 갱신과 완료 후 `fms-carrot-wip-auto` 커밋·푸시를 요청받았다. 기존 WIP·안정 브랜치와 기기 설정은 유지한다. 아래 과거 ‘커밋·푸시 금지’ 기록은 당시 범위이며 최신 auto 브랜치 게시 승인으로 대체됐다. 실측 CPU 배치와 현재 남은 구현은 위 후속 기획을 따른다.

## 승인과 기준

- 수정된 상세계획에 따른 로컬 구현을 승인받았다. 기존 FMS 동작을 보존하며 커밋·푸시·기기 설치·설정 변경은 수행하지 않는다.
- 작업 브랜치: `fms-carrot-wip-auto`. 원격 `fms-carrot-wip`를 조회하고 가져온 기준은 `1b1e019ee6bf5778be751373f269ed4f5d874cf2`다. 별도 worktree에서 이 커밋과 같은 상태로 시작했다. 이전 작업 디렉터리는 보존했다.
- 로컬 remote-tracking ref가 이전 `f76bb193`에 머물러 있어 새 worktree를 검증된 원격 SHA까지 fast-forward했다. 새 커밋이나 merge commit은 생성하지 않았다. 이후에는 ref 이름만 믿지 않고 기준 SHA를 검증한다.
- 상세계획 원본: `FMS_CONTROL_ADVISOR_PLAN_2026-10-25.md`, SHA256 `5006F55F9D21211D822FD568552C16AA4848A74AB37E40C36B04B9F3EA4C2F89`. 기존 Astra High/Max 검토와 WebCarrot 화면 계약을 이어받는다.
- 기존 DM, 정상 ACC 앞차 우선, SoftHold, CANFD 정지 출력, 패들, VEgoStopping, branch 선택 및 재구성 자산은 보존한다. 이 기록은 과거 전체 시험의 재실행이나 실차 검증 완료를 뜻하지 않는다.

## CPU 배치 결정

사용자는 현재 콤마가 연결되지 않았다고 확인했고 코어 지정은 마지막으로 미루는 방향을 제안했다. **분석기 구현 후, 실제 배포·주행 검증 전에** 기기별 측정으로 확정한다. 지금 특정 코어의 여유나 전용 사용 가능성을 가정하지 않는다.

현행 소스의 배치는 다음과 같다. 환경변수·커널·기기 상태를 반영한 실제 readback은 미실행이다.

| 작업 | 소스의 CPU 배치 | 근거 |
|---|---|---|
| WebCarrot | 기본 0–3, `CARROT_WEB_CORES` override | `openpilot/selfdrive/carrot/carrot_server.py:15` |
| planner / card | 4 / 5 | `openpilot/selfdrive/controls/plannerd.py:29`, `openpilot/selfdrive/car/card.py:496` |
| controlsd / modeld | 6 / 7 | `openpilot/selfdrive/controls/controlsd.py:469`, `openpilot/selfdrive/modeld/modeld.py:259` |
| 추정기·로그·통신 | 0–3 공유 | `openpilot/selfdrive/locationd/torqued.py:246`, `openpilot/system/loggerd/loggerd.cc:350` |

CPU 0–3도 빈 코어가 아니다. offroad powersave의 CPU 4–7 offline 경로(`openpilot/system/hardware/tici/hardware.py:306`)도 고려한다. 기존 제어 프로세스의 affinity·RT priority·CPU online 상태를 변경하지 않는다.

코어 선택을 유예해도 다음 설계는 먼저 구현한다: 사용자가 시작한 세션의 별도 분석 프로세스, 비실시간 작업, 유한 입력 큐·메모리·연산량·동시작업 수, 시간 초과/누락 기록, 과부하 시 신규 계산·추천 보류. 실제 수치 상한은 host 예비값과 기기 검증값을 구분한다. 낮은 priority나 affinity만으로 부하 제한이 입증되었다고 표시하지 않는다.

## 폰의 역할과 기존 코드 재사용

기존 승인 범위인 **콤마 경량 1차 분석 → WebCarrot 표시·사용자 적용 → Codex 2차 검토**를 유지한다. 기본 추천 주기는 5분, 선택 3분이며 충분한 근거를 별도로 요구한다. 폰 연결·탭 유지가 기본 수집과 집계의 필수 조건이 되지 않는다. 별도 Android 앱은 추가하지 않는다.

WebCarrot에는 이미 브라우저 분석 기반이 있다.

- `web/src/features/replay/data.js:59–128`: 원본 manifest를 이용한 Worker query/snapshot, 취소 및 worker 종료.
- `web/js/realtime/replay_advanced_worker.js:394–416`: 브라우저에서 zstd/무압축 스트림 또는 bzip2 버퍼를 해제하고 Cap'n Proto 필드를 읽는다.
- 일반 timeline/cache 준비와 route report는 여전히 서버에서 실행한다. `asyncio.to_thread`는 독립 프로세스가 아니므로 서버와 자원 경합을 공유한다.

현행 Worker는 단일 필드 조회·snapshot·그래프 제한용이다. 3분 시간 범위 제한과 표본 상한이 있으며 bzip2는 전체 버퍼 경로다. 이것을 다변량 시간 정렬·개입/회복 제외·5분 추정기로 그대로 사용하거나, 그래프용 감축 자료를 튜닝 입력으로 간주하지 않는다.

선택적 브라우저 보조 분석의 최소 판별은 동일 완료 세그먼트의 필수 필드·원시 시각 일치, 수치/보류 판정 동등성, 전송량·콤마 I/O·CPU·폰 메모리·취소·탭 종료·재연결·다중 탭 소유권이다. 세션/설정 epoch/입력 해시/알고리즘 버전/job generation으로 결과를 연결하고 지연·중복 결과를 거절해야 한다. 브라우저 결과는 적용 권한이 아니며 콤마에서 현재 상태를 다시 검증한다. 실제 이득 확인 전에는 실행 위치를 변경하지 않는다.

## 단계 0 착수 당시 확인과 관문

| 항목 | 현재 근거와 판정 | 다음 종료 조건 |
|---|---|---|
| FMS 기준 보존 | PASS: 원격 SHA 확인, 독립 브랜치 HEAD 일치, 기존 제품 tracked diff 없음 | 이후 변경 파일별 보존·영향 검사 |
| CPU·실행 경계 | PASS: 소스 배치와 브라우저/서버 계산 경계 확인 | 실제 프로세스/스레드 affinity, online CPU, scheduler readback |
| 부하 계측 입력 | 소스 확인: `deviceState` per-core CPU/온도/메모리/thermal, `procLog`, `runtimeTiming` | 분석 OFF/ON 동일 조건에서 지연·dropout·메모리·I/O 비교 |
| 코어 여유·3/5분 예산 | 미실행: 현재 콤마 미연결 | 세션 수집/fit/원본 보존 부하 포함, 실제 기기에서 측정 |
| 실제 로그의 추천 가능성 | 미실행: 조회한 로컬 경로에서 대표 실주행 rlog 미확보; 합성 fixture를 실주행으로 대체하지 않음 | 코드/차량/설정 근거가 있는 완료 rlog로 필수 신호·표본·마스크 검사 |
| 추정 수식·파서·보존 | 미실행 | 순수 계산·고정 seed, 최대 세그먼트/긴 route 메모리·취소, 완료 원본 복사·해시·공간 부족/중단 검사 |
| 저장·실효값·writer 경쟁 | 소스상 미해결 경계 재확인: `services/params.py` RAM fallback 및 별도 `os.replace` 경로 | 실제 Params 확인, 조건부 쓰기·참여 writer·consumer ACK·혼합 전환 검증 |
| 권한·최초 후보 자격 | 미실행 | 신뢰 로컬 활성화/Origin/세션 결합, 항목 profile와 독립 후보 확인 자료 |
| 주차 요약·입력 확정 | 미실행 | 주차/offroad 구분, finalized watermark와 늦은 개입·누락·마지막 segment 검사 |

`procLog.processor`는 마지막 실행 CPU이지 허용 affinity가 아니다. 기존 `runtimeTiming`의 집계 mean/max를 p95/p99로 표시하지 않는다. qlog의 낮은 표본률로 순간 부하가 없다고 결론 내리지 않는다.

현재 진행 가능한 범위는 host의 입력 계약·순수 계산·한도/취소/보존 검증과 비활성 기능 준비다. 기기 미연결 때문에 단계 0 전체가 끝났다고 표시하거나, 미검증 profile·권한·consumer 전환을 통과시켜 주행 적용을 활성화하지 않는다. 실제 부하·코어 선정은 나중으로 유예하되 배포 전 관문으로 유지한다.

## 최초 단계 0 확인 시점

이 파일만 추가했다. 제품 코드·웹 번들·Params·manifest 변경, 기능 시험, native/IPC/ECU/실차 시험은 수행하지 않았다. 기존 제품 소스가 기준 커밋과 같은지 Git diff로 확인했다. 커밋·푸시는 없다.

## PC 구현 후속 — 2026-10-25

사용자가 코어 지정·실기기 검증을 유예하고 PC에서 가능한 구현을 계속하도록 승인했다. 이에 따라 기능 전용 `openpilot/selfdrive/carrot/control_advisor/`에 오프라인 진단 코어, 42개 수치/5개 맥락 파라미터 목록, bounded rlog 입력, 원본 증거 보존, CLI 및 표적 시험을 추가했다. 자세한 실행·제한·제거 경계는 해당 폴더의 `README.md`에 있다.

추가로 torqued의 TLS/마찰 원시 수식을 순수 계산 어댑터로 분리해 Params 접근 금지·고정 seed·원본 수식 동등성을 확인했다. 현재 rlog의 모델 유래 횡반응과 자동 연결하지 않았고, 검증된 숫자 추천으로 승격하지 않았다.

단계 1의 목록·입력 계약과 단계 2의 오프라인 진단·보존 기반을 진행한 것이다. 실시간 수신/sidecar, 독립 횡반응·CAN 송신 복원, 항목별 숫자 후보, writer/consumer 관측, WebCarrot 연결은 미구현이다. 제품 적용은 계속 비활성이며 단계 0 전체를 완료로 승격하지 않는다.

AGY 제안은 허용 외 `manage_task` 도구 요청이 guard에서 거부되어 유효 코드 없이 종료됐다. manager/child 종료와 Codex 인수를 확인했고 권한 확대·재호출 없이 Sol이 구현했다. 이 제공자/도구 오류는 제품 시험 실패 횟수로 세지 않는다.

합성 100Hz·양 축 입력의 host sizing: 3분 36,000 관측은 2.012초/최대 Python 할당 22,992,884바이트, 5분 60,000 관측은 3.664초/38,243,484바이트였다. tracemalloc 계측을 포함한 이번 PC 측정이며 RSS·압축 로그 I/O·파일 복사·후보 fit·콤마 CPU/발열/제어 지연은 포함하지 않는다. 따라서 이 값으로 기기 코어 또는 주행 예산을 확정하지 않는다.

### 오프라인 PC 구현의 종료 확인

- **PASS:** 표적 시험 75개, 실제 CLI의 JSONL/native zstd 입력 → 원본/프로파일 사본 → 결과/완료 manifest → 재검증 흐름. 원본 불변·개입 마스크·출력 제한·단위/특수값·수식 동등성·손상/공간/취소/한도 실패를 포함한다.
- **PASS:** Astra High가 마스크·rlog·보존·CLI를 독립 읽기검토했다. 낮은 심각도의 정상 EOF 거부 1건을 재현했고 수정 후 raw/bz2/zstd의 실제 크기 N부터 N+4까지 성공, N-1·잘림·실제 초과 실패를 확인했다. 남은 확인 범위 내 HIGH/CRITICAL 지적은 없다. 토크 어댑터는 이후 추가된 원본 수식 동등성 시험으로 검증했다.
- **PASS:** 작업 HEAD와 갱신된 `origin/fms-carrot-wip`가 모두 `1b1e019ee6bf5778be751373f269ed4f5d874cf2`, 기존 tracked 파일 차이 0, index 변경 없음. 새 기능 폴더와 이 기록만 추가했다. 커밋·푸시·기기/Params 변경 없음.
- **미실행:** 제품 고정 의존성 환경, 실제 주행 rlog, live 메시지/설정 이력, 소비값 관측, 기기 CPU·RAM·열·제어 지연·원본 I/O. 실차 후보/적용/성능 검증은 수행하지 않았다. 이 조건을 통과하기 전 추천·적용을 활성화하지 않는다.

## 실시간 수신·WebCarrot 연결 단계 (2026-10-25)

이번 승인 범위는 실기기 없이 가능한 수신 구조와 화면 연결이다. 별도 프로세스에서 4개 메시지 서비스를 수신하고 제한된 순서 정렬·개입 마스크·3/5분 진단을 수행한다. 오프라인과 실시간은 같은 `normalizer.py`를 사용하며 오프라인 증거 해시에도 이 파일을 포함한다. native 사용 불가, 늦은 입력, 공백, 과부하는 합성 입력이나 정상 결과로 대체하지 않는다. 합성 데모는 별도 명시 모드이고 가속된 논리 시각을 표시한다.

WebCarrot `로그 → 제어 분석`에 실시간 추천·적용 전후 비교·주차 요약 화면을 연결했다. 기존 대시캠/화면녹화 탭을 유지한다. 현재값 관측·숫자 추천·실제 적용·전후 비교·주차 판정은 아직 준비되지 않았으므로 화면에 제한 사유와 비활성 버튼을 표시한다. 실시간 원본 spool은 미구현이고 메모리 버퍼만 있으므로 보존 완료로 표시하지 않는다. 기존 logger는 변경하지 않았다.

### 구현 선택과 경계

- 기존 웹 broker의 축약값이나 브라우저 감축 로그를 재사용하면 짧은 개입이 빠질 수 있어 별도 non-conflate 구독 작업자를 선택했다. 원시 시각과 수신 시각을 구분하고, 이미 처리한 경계 뒤의 늦은 입력은 기존 진단을 무효화한다.
- 상태 GET는 항상 조회만 한다. 시작·중지는 명시적 환경 opt-in, 메모리에서 사용하는 제어 토큰, 동일 Origin, 제한된 JSON 본문을 요구한다. 단일 작업자/요청 ID/세대로 다중 탭·중복 요청·이전 응답을 분리한다. 서버 종료와 부모 파이프 EOF는 자식 정리로 이어진다.
- 상한은 host 예비값이다: 이벤트 1MiB, 큐 4,096개/8MiB, 관측 100,000개, stdout 256KiB, stderr 보관 8KiB, 요약 6개, 최대 세션 1시간. 협력적 poll 256개/5ms는 단일 native decode의 강제 실행시간 보장이 아니다. affinity·RT priority 변경은 없다.
- 공유 소스 접점은 `server/features/__init__.py`, `web/src/entries/logs.js`, `logs.css` 세 파일뿐이다. 전용 feature/시험과 생성 logs JS/CSS·asset manifest를 연결했다. 재구성 자산도 함께 갱신·검증한다. 제거 순서와 활성화 환경은 기능 README에 기록했다.
- 기존 Astra High/Max 전체계획을 재사용하고 이번 비동기·수신·권한·UI 구현만 Astra High로 읽기검토했다. 무효화된 이전 통계를 현재 유효값처럼 표시하던 P2 결함은 모델·표시·회귀시험으로 보완했다. 자연 종료 때 stdin 감시 스레드가 Python buffered reader lock과 충돌한 문제는 파일 디스크립터 읽기로 수정하고 자연 종료를 별도로 검증한다.

### 검증과 인수 경계

실제 PC 시험 결과는 아래 최종 기록에 기재한다. 모든 시험은 합성/host 입력이며 전체 native WebCarrot 앱이나 실제 기기 검증으로 확대하지 않는다. 기본 경로가 OFF여도 기기에서의 서버 시작·메모리 비용은 현장 확인 대상이다.

남은 구현은 설정 sidecar·consumer 적용 관측, 실시간 원본 보존/복구, 독립 횡반응/CAN 신호, 항목별 후보/검증 프로파일, 실제 적용 권한·조건부 쓰기, 전후 비교와 주차 판정이다. CPU 선택·실기기 IPC·부하/발열/제어 지연은 사용자 결정대로 뒤로 미룬다. 커밋·푸시·배포·기기 변경은 하지 않는다.

최종 PC 검증 기록:
- **PASS:** Python 영향 묶음 113개(26.425초). 기존 native 분석 venv로 실행하고 프로젝트-local aiohttp·공용 NumPy 경로를 해당 시험 프로세스에서만 추가했다. 첫 통합 실행은 새 venv의 CLI 자식에 pycapnp가 없어 실패했으며, 기존 native 환경으로 실행 경로를 바로잡았다. 이는 제품 결함이나 실차 검증 실패가 아니다.
- **PASS:** 웹 표적/기존 로그 영향 시험 37개, 실제 esbuild 생성. 변경 생성물은 logs JS·logs CSS·asset manifest 세 파일이다.
- **PASS:** actual advisor HTTP feature·자식 worker·기존 공용 dialog와 CSS를 사용한 localhost 브라우저에서 합성 진단 수신, 47개 카드/적용 활성 0개, 화면 재열기 시 세션 유지·키 초기화, native 수신 사용 불가/합성 자동 대체 없음, 비교 보류·주차 미확인을 확인했다. 320/390px iframe viewport에서는 주요 진입·버튼·탭의 가로 잘림 없이 표시됨을 시각 확인했다. 이는 전체 native WebCarrot 앱·실기기 시험이 아니다.
- **미실행:** 768/1280px 전체 화면 자동 측정, 실제 기기 IPC/CPU/온도/제어 지연, Params/consumer 적용. Playwright 명령은 자동 정책 검토에서 거부되어 재실행하지 않았으며 허용된 CUA 브라우저로 위 범위를 확인했다.
- **환경:** 프로젝트 .venv에 uv.lock과 같은 aiohttp 3.13.3을 설치했다. 웹 node_modules는 동일 버전의 기존 로컬 캐시를 재사용했다. 제품 의존성·lockfile·전역 환경 변경 없음.

## 설정 관측 이력·원본 보존 연결 (2026-10-25)

사용자는 실기기 없이 가능한 다음 수집 단계를 승인했다. 목표는 저장 설정의 관측 이력과 닫힌 원본 로그의 검증된 사본을 같은 세션·진단에 연결하는 것이다. 기존 Params·logger·제어기는 변경하지 않으며 추천·적용도 계속 비활성이다.

Astra High의 이번 한정 사전 검토로 다음을 확정했다.
- `Params()` 생성 자체가 저장소 생성 경로를 가질 수 있어 생성자를 사용하지 않는다. 명시된 기존 저장 디렉터리에서 목록의 47개 키만 읽는다. 키별 원문·누락/오류·읽기 시작/종료·변경 가능 시간 구간을 기록하고 기본값으로 대체하지 않는다. native params/d의 의도된 루트 링크는 대상을 고정·재확인하고 개별 키 링크는 거부한다.
- 순차 읽기는 원자적 스냅샷이 아니며 polling 사이의 변경·복귀를 모두 검출하지 못한다. `polled_partial / history_complete=false / consumer_applied=false`를 유지한다. 저장값 변경·관측 공백은 현재 진단을 무효화하고 새 관측 revision 구간으로 나눈다. 이번 수집 연결은 공용 진단 창 전체를 보수적으로 분리하며, 무관한 축도 재수집되는 한계가 있다. 이후 항목별 의존성 연결에서 축별 구간을 정교화한다.
- `logger.cc`는 종료 sentinel→rlog.lock 삭제→이전 압축 writer 종료 순서다. 따라서 lock 부재만으로 완료를 판정하지 않는다. 복사본의 엄격한 압축 EOF·시작/종료 sentinel·해시·원본 불변·lock 재확인을 통과한 뒤 완료 manifest를 쓴다. `CurrentRoute`나 파일 mtime만으로 완료·분석 시간 범위를 추측하지 않는다.
- 설정/journal 단일 작성자와 원본 복사 단일 담당을 분리한다. 실시간 IPC 루프는 제한된 큐·상태 전달만 하며 I/O 완료를 기다리지 않는다. archive manifest는 이미 fsync된 journal의 고정 순번·해시와 진단 시각 범위를 참조한다. 완료 사건은 journal 작성자에게 전달해 이중 작성 충돌을 피한다.
- 명시된 설정/원본/보존 루트를 사용한다. 합성 모드는 별도의 DEMO 루트만 쓰며 실제 루트로 자동 연결하지 않는다. 합성 논리 시계와 실제 경과 시간을 구분한다. 저장 공간·파일/이벤트/해제 크기·읽기 예산·큐/종료 상한을 두고 실패 partial은 보존한다. 재시작 검사도 읽기 전용이며 미완료 파일을 임의 수리·삭제·완료 승격하지 않는다.

판별 시험은 디스크 fixture 설정 변경/공백/실패, 복사 중 변경/삭제·공간 부족·취소, lock 부재이지만 압축 미완료, 종료 표식 누락, 다른 세션/진단 이력 오연결, 정상 종료/중단 후 검증, API·화면의 저장값/보존 상태 일치다. 실기기 IPC·저장장치 부하·전원 손실 내구성은 별도 현장 검증으로 유지한다.

### 구현 결과와 선택 근거

`settings_history.py`·`recording.py`·`archive.py`·`inspection.py`를 기능 폴더에 추가하고 기존 worker·세션 API·웹 카드에 연결했다. 공유 제어기·Params·logger·manager 접점은 추가하지 않았다. 이전 단계의 “설정 이력·실시간 원본 보존 미구현” 상태는 이 수집 범위에서 해소됐으며, 실제 consumer 반영 확인과 전체 주행 원본 확보는 여전히 미확인이다.

- 47개 설정과 별도 `CurrentRoute` 관측을 내구 revision에 고정한다. 변경 가능 시각의 하한은 이전 스캔 시작이다. 이미 기록된 진단은 불변 파일의 해시·설정 revision·당시 route를 가지며, 이후 route를 이전 대기 작업에 붙이지 않는다.
- 기존 logger가 되풀이하는 과거 `initData` 시각 대신 `carState` 범위로 원본을 연결한다. 연속 세그먼트·시간순·창 양끝·250ms 경계 조건은 보존 작업 종료용 host 기준이다. 실제 간격과 `coverage_complete=false`를 유지하며, 시간 초과/불충분/취소도 같은 journal에 남긴다.
- 세션 원본 512MiB/128개, 진단 8MiB/128개, journal 8MiB, 보존 루트 누적 4GiB/32세션 및 신규 544MiB 여유를 적용한다. 알려진 partial도 용량에 포함하고 자동 삭제하지 않는다. 설정 스레드 실패 시 복사 종료까지 OS 배타 잠금을 유지한다. CPU affinity·우선순위는 지정하지 않았다.
- 읽기 전용 `inspect-session`은 journal prefix·진단 파일·사본·서로의 참조를 검사한다. 유효 진단의 작업 결과 누락, 완료 이력 이후 사본 소실, 중단된 작업을 성공으로 표시하지 않는다. 같은 route의 동일 원본은 여러 진단에 재사용할 수 있지만 과거 결과를 새 설정의 성능 증거로 재사용하지 않는다.

### 이번 단계 실제 PC 검증

- **PASS:** Python 영향 묶음 181개, 48.965초. 설정 변경·공백·링크/원문·해시 이력, 엄격한 raw/bz2/zstd 종료, 복사 중 변경/재잠금/소실/공간 부족, 범위/route 연결, 큐 거절·시간 초과·취소·OS 잠금·누적 한도·읽기 전용 복구, 실제 worker/HTTP 및 기존 오프라인 분석을 포함한다. 실제 worker 시험은 설정 2800→2900 변경과 원본 불변을 함께 확인했다.
- **PASS:** 웹 영향 시험 46개 및 esbuild 생성. 실제 advisor HTTP·자식 worker·기존 공용 dialog/CSS의 localhost 브라우저에서 관측 revision 1과 내구 revision 1, 검증된 사본 4개/7.8MiB, 보존 이력 확인, 일부 보존·실효값/주차 미확인 표시를 확인했다. 별도 읽기 검사도 검증 사본 4개/무효 사본 0개/미완료 작업 2개/누락 결과 0개와 원본 6개 해시 불변을 확인했다. 가속된 합성 세션의 결과이며 실제 주행 보존률이 아니다.
- **PASS:** 새 카드의 기본 데스크톱·320px 세로 배치를 실제 브라우저로 확인했다. 320px에서 설정·보존 카드의 가로 잘림이 없었다. 시험 서버·브라우저는 종료했다. 기기 화면, 전체 native WebCarrot 실행, 이번 단계의 390/768px 새 카드 검사는 미실행이다.
- **수정 후 PASS:** 초기 진단 revision의 정상 `null`을 서버가 거절하던 통합 결함을 수정했다. 별도의 oversized-output 시험은 30ms timeout과 경합하여 입력 거절 전에 시간 초과가 발생했으므로, 해당 분기만 기존 기본 500ms로 분리했다. 무응답 30ms 검증과 초과 출력 거절 assertion은 유지했다. Astra High가 재현한 변경 시각 하한·과거 initData 범위·route 고정·누락 구간·partial 예산·작업 결과 누락·잠금 조기 해제 경계도 표적 시험으로 수정 확인했다.
- **현장 검증 필요:** 실제 Params 읽기/consumer 반영·native IPC·실주행 rlog의 신호 의미·기기 I/O/RSS/CPU/열/제어 지연·전원 손실 내구성. 숫자 후보 추천·설정 쓰기·전후 효과 판정은 이번 단계에서 구현·활성화하지 않았다. 기존 FMS 제어 경로와 커밋/푸시는 그대로 유지한다.

후속 재검토 조건은 실제 기기의 시각/route/저장소 계약이 host 가정과 다르거나, 부하 예산을 넘거나, consumer 관측·설정 쓰기 단계로 넘어가는 경우다. 제거는 기존 세 공유 import와 전용 기능/시험·생성물·재구성 자산을 함께 갱신하는 기존 경계를 따른다.

## 실제 소비 지점 설정 관측 구조 (2026-10-25)

사용자는 다음 단계로 실기기 없이 가능한 실제 제어기 설정 반영 관측 구조를 승인했다. 이전 181개 Python/46개 웹 시험과 290개 재구성 operation은 직전 단계의 근거다. 이번 변경은 설정을 쓰거나 제어 수식을 변경하지 않고, 기존 소비 객체와 실제 호출 인수를 별도 관측 경로에 전달한다.

Astra High의 한정 사전 검토로 47개 항목의 소비 경로를 모두 등록하고, 저장 관측·읽힌 설정·사용값·생성자 고정·우회·동적 값·미확인을 구분하기로 했다. 같은 항목을 modeld와 controlsd 등 여러 소비자가 다르게 사용하는 경우 각각 남긴다. 소비 사건에 현재 polling revision을 자동 붙이거나 전체 적용 ACK로 승격하지 않는다.

선택한 구조는 기본 OFF의 최소 관측 훅 → 제한된 비대기 큐 → 별도 송신 담당의 JSON/코드 해시/로컬 AF_UNIX datagram → worker 수집기 → 기존 단일 journal 작성자 → API·웹 표시다. 공용 cereal 스키마·logger·manager·Params writer를 바꾸는 대안과 별도 UDP 운영 경로는 기각했다. Windows Python에서 AF_UNIX가 없음을 직접 확인했으므로 native 전송 불가는 그대로 표시하고, PC에서는 명시적인 합성 byte transport를 주입해 계약과 연결을 검증한다.

핵심 판별 조건은 기존 Params 읽기 순서·횟수와 제어 결과 보존, modeld의 이번 action time과 다음 프레임 지연 구분, torque Custom=0일 때 잔류 계수·Kd 효과 없음, 현대/Toyota 고정 종방향 계수, planner의 실제 mode/personality/상태 입력이다. 관측 코드에서 Params나 상태를 바꾸는 계산 함수를 다시 실행하지 않는다. 읽기 시각이 오래된 생성자 값도 이번 사용 표본에서 보유값이 관측됐다면 이를 별도로 표시하되, 읽기 신선도와 사용 표본 신선도를 혼동하지 않는다.

분업은 전용 프로토콜/송수신·수집기, 횡/제어/modeld 훅, 종/planner/carrot 훅, 웹 표시로 나누고 메인이 journal/API·출력 동일성·재구성을 통합한다. 기존 제어 원본 15개 파일의 바이트 사본을 PC 시험용으로 보관했다. 구조 재검토 조건은 consumer 의미나 시각 가정의 반증, 관측이 제어 실행을 기다리게 만드는 경로, 운영 IPC/부하 검증 실패, 실제 쓰기·ACK 단계로의 범위 확대다.

### 연결 결과와 검증 범위

47개 항목을 9개 생산 소스의 10개 소비 지점에 연결했다. `consumer_observer.py`는 기본 OFF이며 활성 시 소비 지점당 최대 1Hz로 기존 객체·호출 인수만 복사한다. 별도 송신 담당이 코드 해시와 JSON을 만들고 로컬 datagram으로 보낸다. `consumer_collector.py`는 boot/PID/실행 인스턴스/순번·시각·크기·필드 allowlist를 검증한다. 큐·전송·factory 오류는 기존 제어 계산으로 전파하지 않는다. 동적 학습값, 플랫폼 고정값, 이전 캐시, 생성자 고정, 현재 경로에서 우회한 값은 저장값과 별도로 기록한다.

작업자는 관측을 같은 단일 journal 작성자에 전달하고 API는 저장값을 덮어쓰지 않은 채 소비 지점별 상태를 공개한다. WebCarrot **로그 → 제어 분석 → 실시간 추천**의 항목 카드에서 `소비 지점별 관측`을 펼쳐 읽힌 값·사용값·출처·신선도를 본다. `실시간 추천`은 기존 화면 이름이며 숫자 후보 추천/적용은 아직 활성화하지 않았다. 원본 개입·회복 구간 보존과 튜닝 계산 제외 규칙은 유지한다.

Astra High의 사전 의미 검토와 최종 실패 경계 검토에서 다음을 보완했다.

- NNFF가 실제 토크 변환 함수를 호출하지 않는 경로에서는 Factor/Friction을 사용으로 표시하지 않는다. 반대로 MPC 내부에서 계산한 값은 최종 lane 경로 선택 여부와 구분한다. modeld의 현재 action에 사용한 지연과 다음 프레임 지연도 분리한다.
- 선택 관측 모듈을 import하지 못해도 기존 planner 생성과 제어 계산은 계속한다. 순번 공백·실행 인스턴스 교체는 다음 연속 표본 전까지 최신값으로 표시하지 않는다. 동일 표본을 반복한 heartbeat는 부모의 관측 유효 시간을 갱신하지 않는다. 수집 종료·강제 중지·자식 실패·heartbeat 중단·2초 초과 후에는 이전 사건은 보존하고 현재값/읽기 신선도를 해제한다.
- journal 전체 8MiB/10,000기록 중 소비 사건은 최대 4MiB/5,000기록으로 제한하고 최종 경계에 64KiB/1기록을 남긴다. 소비 기록 예산이 차면 해당 기록만 거절·partial로 표시하고 설정/진단/보존 영수증은 남은 예산에서 계속 기록한다. 실시간 소비값 수신은 계속된다. 정상 종료 시 예산 거절 수를 남기며 읽기 검사에서 `consumer_observations.budget_rejected_events`로 확인한다. 한 시간 전체 소비 이력 보존을 보장하지 않는다.
- Linux socket 경로 충돌과 native 전송 부재를 구분한다. 비정상 종료로 남은 socket을 자동 삭제하지 않는다. 수집기 종료 확인 후 새 소유 전용 디렉터리와 새 절대 경로로 producer/collector를 함께 재시작하는 복구 절차를 README에 남겼다. 현재는 PC 구조 단계이며 실제 Linux 복구는 현장 검증 대상이다.

PC 검증 근거:

- 초기 통합 동결본의 advisor/server 전체 **221개 PASS**(55.370초). 이후 journal 예산 보완의 설정/기록/읽기 검사 영향 묶음 **55개 PASS**(13.700초), 실제 producer 직렬화 바이트→collector→I/O 작성자→session→HTTP 통합 **1개 PASS**(0.450초)를 추가 확인했다. 부모 신선도·EOF/timeout/강제 중지·요청 재사용의 신규 **7개 PASS**(0.95초), 기존 session **19개 PASS**(6.37초), 수집기 **13개 PASS**(0.025초)를 확인했다. 동일한 시험을 합산해 독립 시험 수로 표시하지 않는다.
- 제어/modeld 소스 6개의 관측 코드 제거 후 전체 AST가 기존 기준과 일치했고, 실제 읽기 순서·횟수와 OFF/ON/발행 실패의 출력 동일성 및 7개 실제 factory의 wire 검증을 확인했다. 관련 host 영향 묶음 **273개 + subtest 12개 PASS**(신규 훅 11개 포함). Carrot/종방향 planner/MPC는 실제 Params 읽기 trace·TF 상태·solver 입력/비용·최종 목표를 비교한 신규 **5개 PASS**, 기존 FMS 표적 **229개 PASS**였다. Linux native root conftest/프로젝트 병렬 옵션을 제외한 명시적인 Windows host harness이며 native solver·실제 Params 검증은 아니다.
- 웹 영향 **54개 PASS**, esbuild 생성 완료. 실제 producer가 직렬화한 바이트를 명시적 합성 transport로 주입한 실제 자식 worker→journal→HTTP→공용 WebCarrot dialog에서 저장값 `2800`과 소비 단위 `2.8`의 분리, 종료 후 사용값 `—` 표시, 적용 비활성, 기본 화면·320px 카드 배치를 확인했다. 두 합성 세션 각각 소비 사건 92개와 완료 원본 사본 4개를 읽기 검사했고 원본 6개 해시는 불변이었다. 전체 원본 범위는 partial로 유지했다. 시험 서버·자식 작업자·브라우저를 정리했다.
- 기존 공용 분석 Python과 프로젝트/공용 패키지를 재사용했다. host 회귀용으로 `uv.lock`과 같은 버전의 pytest 9.0.2·setproctitle 1.3.7을 프로젝트 `.venv`에 설치했으며 제품 의존성/lockfile·전역 설치는 변경하지 않았다.

최종 재구성은 이 문서까지 포함한 **67개 제품 대상**을 동결한 뒤 수행한다. 근거는 저장소 밖 `D:/Codex/오픈파일럿/output/FMS_CONTROL_ADVISOR_2026-10-25/preservation_consumer_worker/`에 남긴다. `modeld.py`·`longitudinal_planner.py` 두 operation의 review bundle 소유권만 새 `fms_control_advisor_consumers.patch`로 옮겨 기존 VEgo 변경과 새 관측을 함께 보존한다. 기존 legacy patch 4개 및 다른 VEgo operation 2개는 그대로 유지하고 helper는 변경하지 않는다. 첫 적용·재적용·실제 checkout·전체 정규화 원본 대조·최종 바이트 동결 결과는 이 디렉터리의 최종 summary가 판정 근거다.

**현장 검증 필요:** 실제 Linux AF_UNIX/권한/복구, native 전체 프로세스/solver, 실차 소비값과 실제 제어 동작의 대응, 콤마 CPU/RSS/I/O/열/지연·전원 손실 내구성. CPU 코어 지정과 기기 실행은 유보했다. 이번 작업에서 Params 쓰기·자동 추천/적용·manager/logger/cereal 변경·커밋·푸시는 하지 않았다. 다음 단계의 숫자 후보와 적용 ACK는 이 관측을 근거로 별도 구현하며 현재 관측 성공을 전체 적용이나 튜닝 성공으로 확대하지 않는다.

## 항목별 후보 계산·검증 구조 — 승인된 다음 단계

사용자는 실기기 없이 가능한 항목별 추천값 계산과 검증 구조의 로컬 구현을 승인했다. 기존 수집·관측 경로와 FMS 제어식은 유지하며 Params writer·자동 적용·코어 지정·기기 실행·커밋/푸시는 범위에 포함하지 않는다. 직전 305개 operation/67개 대상의 검증된 미커밋 작업이 시작 기준이다.

Astra High는 기존 계획과 현재 실제 입력·소비식을 대조해 다음 계약을 확정했다. 현재 네 서비스의 normalizer는 독립 yaw/실제 actuator 추적 자료를 만들지 않으므로 모델 유래 횡반응으로 수치 추천을 활성화하지 않는다. 없는 native MPC/식별 차량 모델을 시험용 RecordingSolver로 대체하는 대안도 기각했다. 47개 모두를 계산 능력·필요 근거·차단 사유로 등록하되, 단순 후보 격자나 코드 연결을 알고리즘 완성으로 집계하지 않는다.

선택한 구조는 명시적인 `EvidencePack + HostProfile → 순수 항목별 계산 → 선택 구간과 분리된 holdout 평가 → 불변 결과 묶음`이다. 저장 raw·소비 인자·물리 단위를 구분하고, 원시 추정/계산 후보/현재값 유지/근거 부족/없는 backend를 나눈다. 특히 SpeedTFFactor 소비 인자 20과 물리 배율 2.0을 일괄 scale로 혼동하지 않는다. 0/−1의 자동·상속 모드와 고정 제어 경로는 보존하며, 숫자 변화로 모드를 바꾸지 않는다. 후보는 한 항목만 바꾸고 현재값도 함께 평가한다. 과거 오토튜너의 비율을 검증된 변경 한도로 사용하지 않는다.

fit/select/holdout 원본·시각 의존 구간의 겹침, 잠정 꼬리, 운전자 개입/회복, 잘못된 단위, 식별 불가, 제약 위반, 양자화 후 효과 소실을 각각 차단한다. 원본은 삭제하지 않는다. 정책 수식 재생의 목표 오차 감소는 차량 추종 개선과 구분한다. threshold·변경 예산은 명시한 host 시험 조건이며 안전 한도나 차량 검증 프로파일이 아니다.

CLI `evaluate-candidates`는 원본 입력 JSON과 프로파일, 코드 해시 및 계산 결과를 새 묶음에 보존한다. 이 단계의 정규화 입력에 선언된 원본 rlog 해시·마스크·설정 epoch는 원본 로그에서 재검증한 연결이 아니다. `raw_sources_verified=false`를 유지하며 실제 주행 추천 권한을 만들지 않는다. 이후 검증된 기록 추출 어댑터가 이 연결을 완성해야 한다.

WebCarrot는 명시한 로컬 결과 디렉터리의 무결성을 검사한 후 별도 **오프라인 후보 계산** 영역에서 표시한다. 실시간 저장값·현재 소비값이나 현재 추천으로 섞지 않는다. 임의 웹 경로 입력·업로드·외부 AI 호출을 추가하지 않는다. 서버 시작 시 제한된 크기의 묶음을 한 번 읽으며 새 묶음 선택은 명시적인 환경 설정/서버 재시작을 사용한다. 모든 결과의 적용·차량 검증 flag는 false다.

분업은 core의 계약/47개 dispatch/양자화/독립 검증, 횡방향 순수 계산, 종방향 정책 수식, 웹 표현으로 분리했다. 메인은 CLI/묶음 무결성/서버 연결과 최종 시험·재구성을 맡는다. 기존 AGY 경로는 앞서 발생한 guard 위반으로 중지된 상태를 유지하고 Sol이 구현한다. 판별용 시험은 알려진 숫자와 반례, 잘못된 단위·겹친 holdout·미래 설정 결합·보호 모드 전환 차단, 실제 CLI→묶음→HTTP→웹 연결이다. 신규 수식이 실제 소비식과 다르거나 raw 신호/목표 독립성이 성립하지 않으면 해당 항목을 재검토하며, 현장 프로파일·실제 적용 단계로 범위를 넓힐 때 별도 검증한다.

### 항목별 계산 연결 결과와 PC 검증

47개 항목의 capability와 필요 신호·단위를 공통 dispatch에 연결했다. 현재 실제 수치 계산 경로는 종방향 정책/정차/차간 수식 22개와 횡방향 Factor·PathOffset·SteerRatioRate·수동 CustomSR 보정 4개다. 마찰 잔차·총 경로 지연 2개는 `diagnostic_proxy`이며 설정 후보로 승격하지 않는다. 나머지는 없는 차량 응답/solver/궤적 평가 경로 또는 맥락·비활성 경로를 명시한다. 이 숫자는 엔진 분기 범위이지, 26개를 실차 추천 가능한 상태로 검증했다는 뜻이 아니다. CustomSR의 현재 메뉴는 raw 0..300/간격 1이며, 과거 inventory의 메뉴가 없다는 설명을 바로잡았다. 기존 수동 raw>10 안에서만 계산하고 자동/수동 모드는 바꾸지 않는다.

Astra High의 독립 검토로 최신 소비 관측 불일치를 버리고 과거 일치값으로 되돌아가던 경계를 보완했다. 기록 입력에서는 해당 키의 모든 등록 소비 지점에 대해 최신 causal event·동일 boot/process/consumer 인스턴스·순번/손실·코드 선언·실제 사용값 일치를 요구한다. 중간 캐시만 일치하고 최종 소비값이 다르거나 필요한 소비 지점이 없으면 보류한다. 파생 사용값의 의미를 아직 대조할 수 없는 경로도 보류하며, 이 검사는 전체 적용 ACK를 만들지 않는다. 신호의 source-to-use 의존 구간이 fit/select/holdout 경계나 개입·회복 마스크를 넘는 경우도 제외한다.

CLI 결과는 입력/프로파일 사본 및 결과 해시를 연결한다. 다른 프로파일을 섞거나 변경된 파일을 같은 해시의 자료로 읽으려는 경우를 거절한다. 실제 사용하는 정책 helper·소비 소스·메뉴 schema와 계산 코드 해시를 기록하고 계산 전후 변경을 검사한다. 이는 입력에 선언한 실제 로그 출처를 자동 인증하는 기능은 아니다. 원본 rlog→typed pack 추출·출처 대조 어댑터는 아직 필요하다.

검증 결과:

- Python advisor/server 277개 중 첫 전체 실행은 274개 PASS, 3개는 시험 실행기의 package context 없는 discovery 때문에 import 오류였다. 제품 수정 없이 같은 3개를 완전한 package 이름으로 실행해 모두 PASS(0.497초)했다. 전체 실행 시간은 63.817초이며, task-local 실행기의 다음 discovery에 top-level 경로를 명시했다. 최초 오류를 제품 결함이나 통과로 집계하지 않았고 최종 277개 고유 검사의 실제 통과 근거를 합쳤다. core 19개, 횡방향 11개, 종방향 11개, CLI/무결성/HTTP 5개가 이 묶음에 포함되므로 중복 합산하지 않는다.
- 웹 영향 63개 PASS, esbuild 생성 완료. 변경하지 않은 다른 생성 파일 22개의 줄바꿈만 기존 checkout 형식으로 복구했다. 실제 CLI가 작성한 `final-example-bundle`을 서버 시작 시 읽고 WebCarrot의 공용 제어 분석 화면에서 47개 결과·43개 비활성 적용 버튼과 합성 CruiseMaxVals0 100→130(1→1.3), PathOffset 0→20(0→0.2)의 후보/단위를 확인했다. 기본 화면과 320px 카드 배치를 확인했다. 표시된 개선은 합성 정책/기하 목적함수의 결과이며 실차 개선이 아니다.
- 원본 제어/소비 생산 파일 9개, Params writer, manager/logger/cereal, 기존 FMS 제어식에는 이번 단계 추가 변경이 없다. 직전 관측 훅의 출력 동일성/읽기 순서 표적 검사 근거를 재사용하며 이번에는 제품 바이트 보존·FMS 재구성으로 동일성을 다시 확인한다.

이번 결과는 `.analysis/control_advisor_candidates/`의 실제 CLI 묶음·전체 검사 로그·package 재실행 로그와 `.analysis/control_advisor_ui/candidate-offline.png`, `candidate-320.png`에 남겼다. 로컬 수치 후보는 웹의 **오프라인 후보 계산 결과** 영역이며 실시간 주행 추천에 자동 연결하지 않는다. 코어/실기기 검증, 기록에서 신뢰 가능한 입력 생성, 미지원 차량 모델/solver, 실제 적용과 적용 전후 인과 비교는 후속 단계다. 추천·적용·기기 자격 flag는 false다.

이 문서와 생성물까지 포함한 77개 대상을 동결한 뒤 저장소 밖 `D:/Codex/오픈파일럿/output/FMS_CONTROL_ADVISOR_2026-10-25/preservation_candidates_worker/`에 첫 적용/재적용/실제 checkout/전체 정규화 대조/바이트 동결 검증을 기록한다. 기존 305개 operation에 새 제품 파일 10개만 추가하며 기존 consumer review bundle 소유권과 legacy 자산을 유지한다. 최종 판정은 해당 폴더의 summary를 참조한다. 커밋·푸시는 하지 않는다.

## 실제 로그와 후보 계산의 자동 연결 — 승인 범위와 계약

직전 315개 operation/77개 대상의 검증된 미커밋 상태에서 이어간다. 사용자는 PC에서 실제 로그를 기존 후보 계산에 자동 연결하도록 승인했다. FMS 제어식·관측 훅·Params writer·기기 실행·코어 지정·커밋/푸시는 변경하지 않는다. AGY 제안 경로는 기존 guard 위반에 따른 중지 상태를 유지하며 Sol이 구현한다. Astra High가 아래 입력·출처·분할 계약을 사전 검토했다.

입력은 명시적으로 선택한 닫힌 rlog 목록, host 계산 프로파일, 목표 정책 파일과 선택적인 기존 기록 세션이다. `evaluate-logs`가 bounded native decode → 기존 개입/회복 마스크 → 검증된 설정 관측 bracket/consumer 결합 → 자동 epoch/fit/select/holdout 분할 → 기존 항목 계산 → epoch별 불변 bundle과 index를 만든다. 수작업 EvidencePack, 현재 PC Params, 현재 가속도를 원하는 가속 정책으로 간주하는 대안은 사용하지 않는다. 모드가 없다고 Normal로 보충하지 않는다.

현재 직접 연결 가능한 양성 경로는 `carState.vEgo`와 `longitudinalPlan.myDrivingMode`, 관련 설정·소비 이력 및 사용자 지정 가속상한 정책 곡선을 사용하는 Cruise 7개 항목이다. 출처를 확인한 다른 직접 신호도 추출하지만, 숨겨진 TF/앞차 응답 상태나 독립 횡응답·좌표/지연 변환을 갖추지 못한 항목은 근거 부족으로 남긴다. 기존 26개 수치 엔진 분기와 모든 47개 항목은 유지하며, 이 단계의 자동 추출 범위를 모든 항목의 자동 추천 완성으로 확대하지 않는다. 정책 수식의 목적함수 개선은 실제 차량 추종 개선이 아니다.

initData의 설정은 초기 참고값으로만 읽는다. session 결합은 닫힌 journal의 hash/sequence/session과 선택 원본의 검증된 archive SHA/크기/route/segment/diagnostic 연결을 요구한다. 이름만 바뀐 원본 사본은 같은 바이트와 유일한 영수증의 연결로 판단하고 모호한 route를 추측하지 않는다. bootlogId를 consumer의 Linux boot UUID로 취급하지 않는다. 관측은 sampled co-origin이며 연속 설정 불변·전체 적용 ACK가 아니다.

같은 설정 revision의 정상 1Hz polling을 매번 별도 epoch로 만들지 않는다. 일정한 설정 bracket을 합치되 scan 중의 불확실한 범위는 eligible_intervals로 구분한다. scan 자체를 운전자 개입으로 재분류하지 않고, 실제 source-to-use 의존이 불확실 범위를 넘는 행을 제외한다. 설정 변경·관측 공백·identity 교체는 구간 경계다. 관련 없는 이후 보존 실패는 앞선 선택 구간까지 자동 무효화하지 않으며, 실제 누락된 소비 관측은 최신 causal/순번/손실/identity/TTL/사용값 검사가 판정한다.

fit/select/holdout은 시간순으로 나누며 서로 의존하지 않도록 경계 여백을 둔다. 전체 원본은 그대로 유지하고, 경계 여백의 행은 원본에 남긴 채 정규화 pack에서는 제외 수와 규칙으로 기록한다. 선택된 block 내부 개입·회복·미완료 행의 mask는 유지한다. 최신 epoch가 보류되어도 과거의 좋은 후보를 최신 결과로 대신하지 않는다. 파일·이벤트·표본·epoch·바이트 한도를 넘으면 조용히 일부만 성공시키지 않는다.

작은 결과 묶음은 생성 입력/프로파일/추출 영수증을 해시로 묶는다. source_bytes_verified_at_generation은 생성 당시 명시 원본 바이트를 확인했다는 뜻이며, 원본 파일을 결과에 내장했거나 원본의 현재 접근·실차 의미·기록 helper 버전까지 인증했다는 뜻이 아니다. raw_sources_verified와 추천/적용/기기 자격은 false를 유지한다. `verify-log-sources`는 사용자가 명시한 원본 목록만 다시 검사하며 웹이 영수증의 로컬 경로를 따라가지는 않는다. 웹서버는 기존 결과 디렉터리 설정으로 단일 bundle 또는 collection을 시작 시 읽어 최신 epoch만 표시한다.

분업은 bounded 신호 추출, 검증된 journal/epoch 연결, 자동 pack/계산 orchestration, 웹의 출처 표시로 분리했다. 메인은 CLI·bundle/index 무결성·시험 통합·FMS 보존 재구성을 담당한다. 완료 조건은 실제 capnp 형식 fixture에서 수작업 pack 없이 숫자 후보까지 연결, 원본 변경·다른 session·미래 관측·최신 설정 불일치·짧은 개입·분할 누수·오래된 후보 대체를 차단하고, 접근 가능한 과거 실로그는 근거에 맞는 보류 결과를 내는 것이다. 실기기·CPU 검증은 계속 유보한다.

Astra 구현 검토에서 두 경계를 추가 보완했다. carState 행 사이에 짧은 설정 scan 공백이 있어도 실제 신호 source-to-use와 의존 여백 전체가 하나의 eligible interval 안에 있어야 한다. 또한 한 collection은 동일 route로 제한하여 서로 다른 부팅의 monotonic 시간으로 최신 결과를 잘못 고르지 않는다. 여러 동일 route 세그먼트는 지원하지만 route/시계 연결이 불명확한 묶음은 실패로 보고한다.

### 자동 연결 결과와 PC 검증

`evaluate-logs`와 `verify-log-sources`, host 예시 프로파일·목표 파일, collection 무결성 검사와 웹의 추출 출처 표시를 연결했다. 동일한 목표 참조 곡선은 canonical 내용 해시로 신호 하나를 공유하고 항목별 목표 ID·점수는 분리한다. 다른 곡선이나 단위는 공유하지 않는다. 실제 60초 로그의 7개 목표를 연결하면서 처음 발생한 4MiB pack 제한 실패를 이 방식으로 해결했으며, 표본 삭제·한도 확대 없이 재검증했다. 실패 산출물은 성공 index 없이 보존하고 새 디렉터리에 성공 결과를 생성했다.

- **PASS — Python 통합 310개:** advisor 전체와 서버 연결을 같은 최종 소스로 실행했다. native capnp 합성 fixture의 실제 설정/소비 journal·보존 영수증을 통해 수작업 pack 없이 후보 raw 130까지 계산했다. 이는 의도한 답이 있는 시험 자료이며 실주행 추천값이 아니다. 최초 통합 실행은 namespace 테스트 디렉터리의 discovery 환경 오류였고, 임시 실행기의 dotted-module 로딩으로 바로잡았으며 제품 검사나 assertion을 생략하지 않았다.
- **PASS — 실제 과거 로그:** 84,174개 이벤트·6,003개 기준 행·60.042초를 읽고 7개 예시 목표로 CLI 계산, collection 무결성 확인, 원본 재확인 CLI까지 통과했다. 중복 제거 후 pack은 2,894,241바이트이며 원본 해시는 전후 동일하다. 설정 관측 세션이 없고 제어 비활성 자료이므로 47개 모두 숫자 후보 없음(보류 42/미지원 5)으로 판정했다. 최초 입력 행은 보존되며 pack 분할 경계에서 제외된 113행은 규칙·제외 수로 기록한다. 여러 제외 사유는 중복되므로 합계를 고유 제외 표본 수로 해석하지 않는다.
- **PASS — 웹 영향 검사 66개:** client/model 및 로그 탭·레이아웃·재생기·요약 경계를 확인했다. 마지막 제외 사유 번역 변경 후 관련 44개를 재검증하고 생성물을 갱신했다. 실제 로그 collection을 기존 HTTP 기능에 연결한 브라우저에서 원본 연결 정보·보류 사유·적용 비활성을 확인했으며 320px에서도 줄바꿈과 스크롤을 확인했다. 기존 단일 bundle은 계속 지원한다.

근거는 `.analysis/control_advisor_log_link/`의 `full-tests.txt`, `native-fixture/collection-final`, `actual-old-log-dedup`, CLI 검증 JSON 및 `actual-log-web.png`/`actual-log-web-320.png`다. 합성 양성 시험과 과거 실제 로그의 음성 판정을 구분한다. 예시 목표 1.3m/s²는 host 시험 입력이며 차량별 권장 설정이 아니다. 원본 파일의 현재 접근성은 명시적 재확인 CLI 결과로만 판단하고 웹은 생성 당시 확인과 현재 미확인을 구분한다.

최종 문서·생성물 포함 85개 대상은 저장소 밖 `D:/Codex/오픈파일럿/output/FMS_CONTROL_ADVISOR_2026-10-25/preservation_log_link_worker/`에서 재구성·재적용·실제 checkout 대조한다. 기존 315개 operation에 신규 파일 8개만 더하고, 기존 9개 소비 관측 삽입 파일·legacy 자산·공유 consumer review bundle 소유권을 보존한다. 최종 수치 판정은 해당 폴더의 `final_summary.json`을 따른다.

**후속/현장 검증 필요:** 실행 중 세션에서 완료 로그를 자동 예약해 주기적으로 계산하는 경로, 항목별 부족한 native 신호/차량 응답 모델, 실차 프로파일 검증, 실제 설정 적용·전후 인과 비교, 콤마 CPU/RSS/I/O/열과 코어 배치. 이번 단계는 명시한 닫힌 로그에서 오프라인 계산까지 자동화한 것이며 실시간 수치 추천·설정 적용을 활성화하지 않는다. 기존 FMS 제어식·관측 훅·Params 쓰기·기기 설정·커밋·푸시는 변경하지 않았다.

## 완료 로그의 주기 분석·웹 갱신 — 승인된 다음 단계

사용자가 완료 로그의 주기 자동 분석과 웹 결과 갱신을 승인했다. 시작 기준은 직전 323개 operation/85개 대상/6,344경로의 검증된 미커밋 상태다. 기존 FMS 제어식·관측 훅·Params 쓰기·CPU 배치·실기기·커밋/푸시는 변경하지 않는다. AGY 경로는 기존 guard 위반으로 중지한 상태를 유지하고 Sol이 구현한다.

Astra High 검토에 따라 활성 기록 세션에서 **완료 archive의 journal 기록까지 fsync된 뒤의 확정 prefix**를 분석 입력으로 사용한다. writer는 작은 checkpoint만 전달하고 분석 child가 해당 바이트 길이의 불변 사본·체인·참조를 검증한다. 세션 종료를 기다리는 방식은 주행 중 결과 갱신 요구에 맞지 않으며, 전체 활성 journal 복사·가짜 session_closed 추가·기존 closed 검사 제거는 일관성을 깨므로 기각했다. 마지막 오른쪽 poll까지 완성된 설정 bracket만 사용하며 prefix 확정은 소비자 이벤트 전체 도착이나 설정 적용 ACK가 아니다.

분석 기능은 명시적으로 설정해야 켜진다. 기존 180/300초 선택을 재사용하고, 계산은 별도 child에서 하나씩 수행한다. 대기 입력은 최신 한 건으로 합치며 입력/설정/코드 신원으로 중복·재시작을 구분한다. 분석 결과와 원본 보존은 별도 예산을 적용하고, 원본을 삭제하거나 미완료 결과를 성공으로 표시하지 않는다. 검증된 immutable 결과만 부모가 원자적으로 게시하며 종료·이전 실행의 늦은 결과는 최신 게시를 덮어쓰지 못해야 한다.

웹은 기존 status 조회를 재사용한다. 서버의 낮은 빈도 background 갱신과 메모리 캐시로 HTTP 요청 중 분석을 피하고, 대기·처리 중·실패·오래된 결과·새 결과를 구분한다. 기존 정적 보고서 경로는 유지하며 경로 입력/업로드/설정 적용 권한은 추가하지 않는다. 실차 추천/적용/기기 검증 flag는 false를 유지한다.

분업은 prefix·history 연결, 분석 프로세스와 게시, recording lifecycle, 서버·웹 갱신으로 나눴다. 판별 시험은 활성 journal append 중 prefix 생성, 잘린/변조된 입력·선행 영수증 없는 segment 차단, 동일 입력 중복·재시작·동시 소유·분석 중 종료·quota/timeout·최신 실패 표시, native 형식 fixture의 완료 로그부터 HTTP/UI 갱신까지다. 메인은 통합·문서·기존 FMS 보존 재구성을 확인한다. 실제 콤마 부하·native IPC·주행 성능 검증은 이 PC 시험으로 대체하지 않는다.

추가 용량 검토에서 실제 60초 로그가 84,174 events/두 번 해제 약111MB/pack 약2.9MiB였음을 확인했다. 3/5분 전체를 한 pack으로 합치면 기존 이벤트·해제·pack 한도에 걸릴 수 있어, Astra는 선택된 전체 입력을 명시적인 segment별 분석 window로 나누도록 권고했다. 같은 원설정 epoch와 checkpoint는 유지하고 각 window 안에서 fit/select/holdout을 별도로 검증한다. 이는 서로 다른 설정 변경이나 전체 주기의 통합 최적값이 아니다. 개별 window가 한도·근거 조건을 통과하지 못하면 보류 상태를 남기며, 전체 묶음의 일부 누락을 숨기거나 과거 성공을 최신으로 대신하지 않는다. 한도를 늘리거나 원본·표본을 조용히 줄이는 대안은 사용하지 않는다.

최종 경계 검토는 두 보완점을 확인했다. 새 poll/consumer 행만 추가된 경우와 새 완료 원본이 추가된 경우를 별도 watermark로 구분하여 불필요한 child/decode/job 생성을 막는다. prefix의 기대 archive 목록은 검증 실패한 사본도 유지하여 중간 누락을 전체 성공으로 표시하지 않는다. 미검증 사본의 진단 요청 범위를 실제 carState 범위로 승격하지 않는다. 일시적인 causal consumer/right bracket 누락이 실제 결과에 나타났을 때에만, 새 checkpoint가 있는 다음 정규 주기에 동일 원본·프로파일·코드의 settling 재시도를 1회 허용하고 소진을 재시작 뒤에도 유지한다. 구조적 신호/백엔드 부재·오래된 관측·모순은 이 재시도 조건이 아니다. 1회 이후 늦게 도착한 근거는 새 archive 또는 명시적 재분석 전까지 반영하지 않는 한계를 남긴다.

### 주기 분석·화면 갱신 구현 결과

`RecordingSession`의 선택적 세 번째 담당 스레드가 fsync된 archive watermark와 최신 checkpoint 하나를 전달한다. 기본 OFF에서는 기존 두 기록 스레드와 동작을 유지한다. 별도 `PeriodicAnalyzer`가 180/300초 간격의 단일 child를 소유하고 완료 결과를 검증한 뒤 최신 포인터를 원자적으로 게시한다. 새 원본이 없는 일반 poll에서는 child·작업 디렉터리·결과 버전이 늘지 않으며 재시작에도 완료/재시도 소진 상태를 유지한다. 자동 결과 루트가 설정·원본·보존 루트와 상·하위 포함 겹치면 자동 분석만 실패로 두고 기존 기록을 계속한다.

각 window는 기존 이벤트/해제/pack 한도를 유지한다. 별도 host 예산은 cache 512MiB/32작업, 작업당 120초·8개 window·출력128MiB다. 일부 window 실패와 기대 사본 누락은 partial로 게시하고 미확인 범위는 null로 남긴다. 숫자 후보가 없더라도 계산과 검증을 정상 수행했다면 window 계산은 완료일 수 있다. 계산 완료 수는 추천 가능한 항목 수나 차량 성능 개선 수가 아니다. 원본·이전 완료·실패 산출물을 자동 삭제하지 않는다.

웹서버는 5초 간격의 단일 background reader와 메모리 캐시를 사용한다. 상태 heartbeat만 달라지면 큰 결과 묶음을 재검증하지 않으면서 최신성은 매번 확인한다. 화면의 기존 2초 조회가 게시 버전·대상 범위·완료/보류/실패 수를 갱신한다. 처리 중·partial·실패·통신 단절·최신성 미확인·종료 상태에서는 이전 숫자 후보를 최신으로 표시하지 않는다. 정적 보고서 경로는 유지하며 자동 경로와 동시 지정하면 설정 충돌을 알린다.

- **PASS — 최종 Python 통합 346개/185.563초:** advisor 전체와 서버 연결을 같은 최종 소스로 실행했다. prefix 동시 append·변조·fsync 실패, 기본 OFF, 수집 연결과 root 충돌, 단일 child·재시작·중복 억제, 실제 Windows 부모 강제 종료 후 자식 회수, timeout·정상 종료·quota 보존, cache/최신성·HTTP 호환을 포함한다.
- **PASS — 180초 native 형식 생성 자료:** 원본 사본 3개를 별도 child로 모두 계산해 window3/처리3/보류0과 각 후보 raw130을 확인했다. 중간 사본 누락·손상은 각각 대상3/처리2/차단1/partial이며, 범위가 없는 사본은 진단 범위로 보충하지 않는다. late causal consumer 추가 후 한 번의 settling 재시도에서 후보 없음→130, 두 번 계산 뒤 재시작에서도 소진 유지가 확인됐다. 이 값은 의도한 답이 있는 생성 자료의 결과이며 실차 추천값이 아니다.
- **PASS — 웹 영향 69개:** 앞선 영향 묶음68개와 추가 모델 반례1개를 포함한다. 마지막 client/model47개를 재검증하고 웹 번들을 갱신했다. 실제 publisher→실제 reader→HTTP→브라우저에서 결과 버전1/후보130을 확인한 뒤, 별도 시험용 목표 사본만 바꿔 버전2/후보120으로 열린 화면이 자동 갱신됨을 확인했다. 서버·페이지 재시작은 없었으며 320px 표시와 정상 종료 상태 자동 전환도 확인했다.

통합 로그는 `.analysis/control_advisor_periodic/full-tests.txt`, 생성 입력은 `native-fixture/fixture-info.json`과 `checkpoint.json`, 실제 게시 결과는 `browser-publisher-result-1.json`/`browser-publisher-result-2.json`에 있다. 화면 근거는 `periodic-web-revision2.png`와 `periodic-web-320.png`다. 원본 입력과 이전 결과를 보존했고 시험 publisher·브라우저·HTTP 프로세스는 정리했다. 실제 차량·콤마 부하·전원 손실·native IPC·ECU 동작은 검증하지 않았다.

최종 문서·생성물까지 포함한 91개 대상을 동결하고 `D:/Codex/오픈파일럿/output/FMS_CONTROL_ADVISOR_2026-10-25/preservation_periodic_worker/`에서 재구성/재적용/실제 checkout/전체 정규화/raw 동결과 기존 9개 생산 훅·legacy·consumer 소유권을 확인한다. 기존323개 operation에 신규 파일6개만 추가하며 최종 판정 수치는 해당 `final_summary.json`을 따른다. 기기 CPU 코어 지정·실차 검증과 차량별 자격·실제 설정 적용·적용 전후 인과 비교는 여전히 남은 단계다. 커밋·푸시는 하지 않았다.

## 추천값 적용 요청·전후 비교 구조 — 승인된 다음 단계

사용자는 실기기 없이 가능한 적용 요청과 전후 비교 구조를 승인했다. 시작 기준은 직전329개 operation/91개 대상/6,356경로의 검증된 미커밋 상태다. 기존 FMS 제어식·9개 생산 관측 훅·CPU 배치·실기기 설정·커밋/푸시는 변경하지 않는다. `apply_enabled / recommendations_enabled / device_qualified=false`를 유지하고 요청 등록과 PC 모의 실행의 capability를 별도로 구분한다. 기존 AGY guard 중지 상태는 유지하며 Sol이 구현한다.

Astra High 설계는 검증된 후보에 결합한 내구성 요청 처리기와 격리된 PC 모의 저장소를 선택했다. 모든 버튼을 비활성으로 남기는 대안은 요청·비교 구조의 완료가 아니며, 기존 Params 서비스의 메모리 fallback이나 무검증 차량 writer를 사용하는 대안도 기각했다. 웹은 임의 새 값·파일 경로·adapter를 받지 않고 요청 ID/대상/세션/산출물 해시/결과 revision/항목/예상 현재값·설정 revision·의존 digest만 전송한다. 숫자는 서버가 재검증한 immutable 후보에서 가져온다. 47개 항목은 동일한 capability·근거 판정을 적용하며, 숫자 후보 없음·진단 대리값·모드 전환·미지원 경로는 이유를 표시한다.

차량 대상 요청은 `held: vehicle_writer_unavailable`로 기록하고 자동 재개하지 않는다. PC 모의 scope는 서버 시작 시 명시적으로 주입한 격리 저장소만 사용한다. 요청/intent를 먼저 fsync하고 현재값·revision·의존값을 CAS 검사한 뒤 값·새 revision·request ID를 원자적으로 저장한다. 요청값을 ACK로 복사하지 않고 별도 관측 경로가 저장소를 읽어 소비 관측을 만든다. 저장됨과 소비 관측됨, 비교 가능은 다른 상태다. 동일 request ID의 같은 내용은 재사용하고 다른 내용은 충돌이다. 실행 직전 최신 후보·만료·대상·설정을 다시 확인하며, intent 전 취소만 확정할 수 있다. 중단 후에는 request ID와 값이 묶인 실제 저장 영수증으로만 복구하고 값만 우연히 같다고 성공으로 추정하거나 자동 재실행/원복하지 않는다.

전후 비교는 요청 시 before 근거와 host profile을 고정하고, 필요한 소비 지점 중 가장 늦은 반영 관측 뒤의 after만 선택한다. 변경 불확실 구간·전환 여유·운전자 개입 직전/중/회복·미완료 꼬리를 제외한다. 대상·세션·route·boot·코드·제어 경로·모드·단위·목적이 맞아야 하며 다른 관련 설정이나 학습/의존 조건이 함께 바뀌면 해당 요청만의 효과 판정을 보류한다. 명시한 속도·목표 등 조건 대역의 공통 표본에서 기존 bias/RMSE/p95·유효 시간·제외 사유를 계산한다. 정책 목적함수나 모델 유래 횡응답을 독립 실차 추종 개선으로 바꾸지 않고, 전후 관측 차이를 인과효과나 전체 적용 ACK로 확정하지 않는다.

소유 경계는 요청 ledger·상태기계·PC store, 검증된 후보/원본 근거 resolver, 비교 계산, 서버/API/웹으로 나눈다. 완료 조건은 PC의 클릭→내구성 요청→모의 CAS→별도 소비 관측→전후 비교 연결, 실제 vehicle 요청의 저장된 보류 상태, 중복/충돌·stale 후보·CAS 실패·intent 전후 crash·재시작·늦거나 누락된 관측·다중 변경·개입·조건 부족 차단이다. 메인은 통합·사용 안내·FMS 재구성을 확인한다. 실기기 부하·차량 writer 자격·실제 도로 성능·코어 지정은 계속 유보한다.


### 적용 요청·전후 비교 구현 및 PC 검증 완료 (2026-10-25)

`apply_requests.py`, `request_evidence.py`, `comparison.py`와 각 표적 시험을 추가했다. 기존 세션 제어기의 신선한 요청 binding, 서버의 등록/조회/취소 API와 단일 background 담당, WebCarrot 실시간 카드·요청 이력·비교 화면을 연결했다. `CARROT_CONTROL_ADVISOR_REQUEST_ROOT`는 별도의 opt-in 요청 기록 루트이며 실제 Params writer 환경변수나 writer import를 추가하지 않았다. PC 모의 저장소와 비교 provider는 서버 생성 시 명시적으로 주입한다. 차량 대상은 내구성 있는 `held / vehicle_writer_unavailable`로 끝나고 자동 재개하지 않는다.

요청과 CAS는 hashchain journal·fsync·단일 작성자·조건부 저장을 사용한다. 불확실한 기록 실패 뒤 작성자를 재사용하지 않으며, 재시작의 미실행 registered 요청은 재확인이 필요한 terminal 상태로 끝난다. intent 후 복구는 request ID와 실제 저장 영수증이 함께 맞을 때만 인정한다. 소비 관측과 비교는 저장 영수증과 별도로 검증하며, 요청 시 고정된 before 선택자·비교 profile 전체와 hash를 사용한다. 이후 profile 변경이나 재시작으로 과거 요청을 새 기준으로 재해석하지 않는다.

전후 비교는 실제 보존 사본과 frozen journal prefix에서 정규화한 관측을 사용한다. 전환 경계 전의 target/actual/carControl 출처 시각, 개입·회복·관측 공백과 설정 불확실 구간을 넘은 의존 신호를 제외한다. 종·횡 각 소비 경로의 실제 맥락을 구분하고, 토크 Factor/Friction 및 다른 캐시 게인의 학습/실효값·출처·상태가 함께 변하면 단일 항목 비교를 보류한다. 실제 after route/boot가 다르거나 heavy 계산 중 최신 게시 상태·설정이 바뀌어도 바로 보류한다. 정책 적합도를 실제 추종 개선으로 바꾸거나 관측 차이를 인과효과로 확정하지 않는다.

같은 불변 자료의 반복 review는 native 재디코딩을 생략한다. 캐시는 상태 전이를 제외한 전체 application과 before/after/profile에 결합하며, 허용된 상태 전이에서 영수증의 application hash만 현재 상태에 맞춰 다시 결합한다. baseline·target 변경은 캐시를 재사용하지 않는다. 실제 브라우저에서 첫 비교 뒤 자동 갱신 시 `comparison_receipt_digest_mismatch`가 발생하는 것을 찾아 이 경로를 수정하고 연속 review 및 HTTP poll을 재검증했다. 서버 갱신이 실패한 화면은 이전 지표·영수증을 보존하면서 각 카드에 `이전 결과 · 현재 상태 미확인`을 표시한다.

검증 결과는 다음과 같다.

- Python 통합 **390 tests PASS / 201.838초**, `.analysis/control_advisor_apply/full-tests.txt`. 새 요청·비교·원본 연결과 기존 수집·후보·서버/세션 시험을 함께 실행했다. 관련 웹 **76 tests PASS**(client/model 54 + 기존 로그/재생/요약 영향 22), Node 빌드 및 기존 미변경 생성물 22개의 checkout 형식 복구 완료.
- 실제 WebCarrot markup/CSS/module과 실제 HTTP feature를 사용한 CUA 검증: 세션 키 전 등록 비활성 → 키 입력 후 실시간 카드의 연결된 로그 후보 `100 → 130` 요청 → PC 저장 revision 1 → 소비 관측 전에는 `stored`/관측 false/비교 false → 별도 모의 소비자가 저장소를 읽고 두 번째 native 로그·archive·prefix·publication 생성 → `comparison_ready` → 후속 poll 3회 모두 request 상태 ready. 최신 게시 버전은 2이고 다음 후보가 없을 때 재요청 가능으로 남기지 않는다.
- 생성 자료의 비교 표본은 before 113개/5.15초, after 102개/4.65초, 맞춘 RMSE는 약0.1/0.02m/s²다. 이는 명시적인 벤치 응답식으로 만든 검증값이며 **차량 권장값이나 실제 주행 개선율이 아니다**. before 원본 해시 불변, 실제 vehicle-scope 등록의 durable held와 원래 저장값 불변도 확인했다.
- 최종 통합 증거는 `.analysis/control_advisor_apply/browser-run-5/verification-summary.json`, `browser-before-consumer.json`, `browser-final-result.json`, `vehicle-held-result.json`이다. desktop 및 320px 좁은 화면 배치를 확인했으며 화면 증거는 같은 상위 폴더의 `apply-compare-desktop.png`, `apply-compare-320.png`다. 초기 PC harness의 Windows status rename 경합과 구형 fixture/검증 스크립트 계약 오류는 제품 성능 실패로 계수하지 않았다. 생성 publisher의 heartbeat 재시도는 기존 10초 TTL을 연장하거나 실패 상태를 정상으로 바꾸지 않는다.

Astra의 독립 검토에서 발견한 초기 PC revision, fsync 실패 후 재사용, 재시작 TTL, 서로 다른 의존 digest, 변경 전 신호의 after 포함, 계산 도중 최신성 변화, route/boot 결합 경계를 수정했다. 추가로 실제 통합에서 비교 profile 동결, 학습 실효값 변화, 반복 poll 캐시 영수증, 실시간 카드 버튼 접근성과 갱신 실패 표시를 보완했다. 실제 기기 연결·CPU 코어/부하·차량 writer 자격·실제 도로 성능·주차 판정은 검증하지 않았으며 계속 남은 단계다. 이번 작업에서 생산 관측 훅 9개, 기존 FMS 제어식, legacy 오토튜너와 commit/push 상태를 변경하지 않았다.

FMS 보존 대상은 기존91개에 새 모듈·시험6개를 더한97개다. 최종 재구성/재적용 판정은 `D:/Codex/오픈파일럿/output/FMS_CONTROL_ADVISOR_2026-10-25/preservation_application_worker/final_summary.json`을 따른다. 전용 코드와 웹/서버 접점을 제거할 때에는 해당 보존 자산도 함께 갱신하며, 외부 원본·요청 ledger는 코드 제거만으로 삭제하지 않는다.

## 주차 요약·Codex 2차 검토 내보내기 — 승인된 다음 단계

사용자가 승인한 범위는 PC에서 검증할 수 있는 주차 관측, 누적 요약, 로그·설정/소비 관측·적용 이력·분석 결과의 로컬 내보내기다. 시작 기준은 335 operations / 97개 대상 / 6,368 정규화 경로다. 기존 제어식·생산 관측 훅·실기기 writer·CPU 코어 배치와 커밋/푸시는 변경하지 않는다. AGY 경로는 기존 중지 상태를 유지하고 Sol이 구현한다.

Astra High는 주차 관측, 기기 offroad, 분석 확정 범위, 원본 꼬리 완결을 각각 표현하도록 검토했다. fresh·valid한 P 기어/정지/제어 비활성을 host profile 동안 관측하면 주차 요약을 열 수 있지만, 신호 대기 중 0속도·세션 종료·offroad만으로 주차를 판정하지 않는다. `deviceState`는 보조 입력으로 수신하며 기존 네 제어 서비스의 freshness를 대신하지 않는다. 주차 때문에 logger나 세션을 강제로 종료하지 않으며, 종료 후 현재 관측은 unknown으로 내리고 과거 요약을 유지한다.

요약은 누적 캐시로 먼저 제공하고 세션·revision·분석 확정 범위와 미완료 꼬리를 명시한다. 원본이 없는 세션도 요약과 누락 목록을 내보낼 수 있다. 사용자는 고정된 요약 revision과 원본 포함 여부만 선택하며 서버 경로를 입력하지 않는다. 원본 포함은 기본 해제이고 위치·식별정보가 남을 수 있는 민감 자료임을 표시한다. 외부 업로드와 Codex 자동 호출은 없다. 종료·TTL이 지난 결과는 역사 자료로만 보존하며 현재 추천이나 적용 권한을 되살리지 않는다.

내보내기는 단일 백그라운드 작업으로 고정 prefix와 immutable 결과를 복사·해시 검증하고 manifest와 ZIP 완결 표시를 마지막에 게시한다. 파일별 상대 경로·크기·SHA·역할·민감도와 누락 사유, 코드/스키마/profile 참조를 담는다. 무결성 확인과 자료 범위 완전성은 별개다. 취소·중단·한도 초과는 완료로 표시하지 않고 원본과 이전 완료 파일을 보존한다. 다운로드에도 기존 인증을 적용하며 장기 토큰을 URL에 넣지 않는다.

분업은 주차 수신/상태, 불변 요약/참조와 읽기 전용 요청 ledger, export/독립 검증, 서버/웹으로 나눈다. 완료 조건은 실제 PC HTTP·WebCarrot의 요약→명시적 내보내기→인증 다운로드→원래 저장 위치 없이 ZIP 검증이다. 주차 반례·신호 만료·열린 꼬리·원본 포함/제외·없는 원본·변조/취소/재시작·무권한 다운로드를 표적 검증한다. 실제 차량 신호 신뢰도·콤마 부하·전원 차단 내구성은 현장 검증으로 남긴다.

### 주차 요약·내보내기 구현 및 PC 검증 결과 (2026-10-25)

`parking.py`, `parking_summary.py`, `review_export.py`와 표적 시험을 추가하고 기존 live/session, 요청 ledger의 읽기 전용 snapshot, 서버/API, WebCarrot 주차 탭과 CLI를 연결했다. 실제 `deviceState`의 2Hz 발행 주기를 확인하고 offroad 신호는 보조 입력으로 받는다. host profile은 P/standstill/정지 속도/제어 비활성 유지 2초, carState 250ms·carControl 200ms·deviceState 2초의 관측 유효 범위를 사용한다. 원본 시각과 수신·처리 시각을 함께 검사한다. 정지 신호·주차·offroad·수집 종료는 서로 대체하지 않으며, 종료 뒤 현재 주차는 unknown이고 마지막 관측 기록만 보존한다. 분석 watermark는 주차나 종료 때문에 승격하지 않는다.

요약은 최대 64개 불변 버전으로 보관하며 단순 heartbeat·나이·수신 건수 때문에 버전을 늘리지 않는다. 설정/분석/요청 상태의 변경과 관측 전환을 반영한다. 겹치는 최근 분석 창의 합산을 피하고 최신 진단의 관측·유효 시간, 원시·유효 오차, 제외·잠정 표본을 구분한다. 후보의 checkpoint가 현재 세션과 맞지 않으면 이전 세션 또는 미연결 자료로 표시하고 후보 집계에서 제외한다. 선택 당시의 job/window/bundle·설정 prefix·요청 ledger를 고정하며 이후 latest 포인터로 바꾸지 않는다. 재현할 참조가 부족한 과거 비교는 누락 사유를 남긴다.

`CARROT_CONTROL_ADVISOR_EXPORT_ROOT`는 기본 OFF인 별도 출력 루트다. 내보내기 작업은 하나씩 실행하고, 최대 32개 작업/전체 1GiB와 작업당 512MiB·512파일·120초의 host 예산을 사용한다. payload와 ZIP을 함께 용량에 계산한다. 선택 단계도 metadata 읽기 64MiB·256파일·32개 checkpoint·deadline/cancel을 제한하고 원본 해시는 실제 포함 시 복사 단계에서 검사한다. 이 값은 기기 자원 측정값이 아니다. 실패·취소 파일은 자동 삭제하지 않으며 재시작은 완료 파일 재검증 또는 interrupted 처리만 한다.

API는 기존 상태 조회에 캐시된 요약/작업 상태를 더하고 `GET /summary`, 인증된 `POST /export-jobs`, 개별 작업 조회·취소·다운로드를 제공한다. 생성 본문은 `request_id`, `summary_id`, `summary_revision`, `include_raw` 네 필드뿐이다. 다운로드는 기존 토큰·Origin을 검사한 POST 후 해당 작업에만 유효한 60초 HttpOnly/SameSite 쿠키로 파일을 스트리밍한다. 장기 토큰을 URL에 넣거나 큰 ZIP을 브라우저 Blob에 통째로 올리지 않는다. 임의 경로·외부 업로드·Codex 호출·차량 설정 쓰기는 추가하지 않았다.

검토 ZIP은 고정 설정/소비 관측 prefix, 요청·비교 이력, 후보 입력·profile·결과, 진단·archive 영수증, 요약, 원본 포함/제외 및 누락 목록을 담는다. 원본을 선택한 경우 확인된 닫힌 rlog 사본을 변경 없이 넣는다. `verify-review`는 원래 경로를 읽지 않고 파일 SHA·prefix chain·ZIP 구조와 manifest 결합을 검사한다. 파일 무결성, 자료 완전성, 현재 추천 자격과 차량 검증은 별도이며 후자의 자격 flag는 false를 유지한다.

- **PASS — Python 통합 429개 / 220.095초:** `.analysis/control_advisor_review/full-tests.txt`. 기존 수집·후보·적용 요청·비교와 새 주차/요약/내보내기를 함께 실행했다. 마지막 자원 집계·요약 캐시 보완은 담당자의 후속 표적 검증을 적용했으며 같은 전체 검사를 반복하지 않았다.
- **PASS — 웹 81개:** client/model 59개와 기존 로그·재생·요약 영향 22개. Node 빌드와 변경 없는 생성물 22개의 checkout 형식 복구 완료. 공개 상태 캐시·인증 전 비활성·원본 기본 제외·내보내기 본문/경로·다운로드 grant 연결을 확인했다.
- **PASS — 실제 HTTP·브라우저:** 생성 native 자료의 PC 저장→별도 소비 관측→비교 결과를 기존 경로로 만든 뒤 주차 신호를 관측했다. 주차 탭은 생성 자료 표시, 저장/소비/비교 1/1/1, 부분 진단과 마지막 잠정 구간을 표시했다. CUA에서 원본 제외 ZIP 30파일과 원본 포함 ZIP 32파일을 각각 만들고 실제 Downloads 파일을 받았다. 원본 포함 묶음의 rlog 2개는 내보내기 전 원본 SHA와 일치했다. 원본 폴더 접근을 감사 hook으로 차단한 CLI 검증에서 원본 접근 시도 0, 두 ZIP 모두 verified, 누락 없이 선택된 실제 후보 입력/profile/진단/ledger/영수증 포함을 확인했다. 무권한 다운로드는 403이다.
- **PASS — 종료 후 보존:** 실제 수집 종료는 현재 주차 unknown·주차 확인 false·과거 주차 기록 유지로 전환했다. publisher stopped 뒤 현재 추천을 되살리지 않고 새 역사 요약을 내보내기 완료했다. desktop과 320px에서 카드·버튼·문구 줄바꿈을 확인했다.

Astra 독립 검토의 원본 시각 freshness, 폴더 검증과 실제 ZIP 검증의 불일치, 이전 세션 후보의 새 요약 혼입을 수정했다. 실제 브라우저 시험은 Windows 260자 초과 내보내기 경로에서 파일 생성 실패를 드러냈고, OS 설정을 바꾸지 않고 검증된 내부 파일 I/O에만 extended-length 경로를 적용했다. 같은 실패 자료의 생성/폴더/ZIP 검증과 긴 경로 회귀가 통과했다. 최종 긍정 근거는 `.analysis/control_advisor_review/browser-run-4/download-verification.json`, `browser-summary-only.json`, `browser-raw-ready.json`, `stopped-status.json`, `stopped-export-result.json`이며, 화면은 `parking-export-desktop.png`, `parking-export-320.png`다. 앞선 run1–3의 부분 실패 산출물도 보존한다.

FMS 보존 대상은 기존97개에 새 모듈·시험7개를 더한104개다. 최종 재구성·재적용·정규화·raw drift 판정은 `D:/Codex/오픈파일럿/output/FMS_CONTROL_ADVISOR_2026-10-25/preservation_review_worker/final_summary.json`을 따른다. 실제 콤마 IPC·CPU 부하/코어 지정·실차 주차 신호·전원 차단 내구성·도로 성능은 미검증이다. 커밋·푸시는 하지 않았다.
## Codex 2차 검토 결과 연결 — 승인된 다음 단계

완료된 내보내기의 manifest·요약 SHA와 요약 ID/revision, 검토 대상별 result.json SHA에 검토 기록을 결합한다. Astra High는 단일 결과 해시를 요약 해시로 대체하지 않고 `base_results` 목록을 사용하는 계약을 확정했다. 과거 묶음도 검토할 수 있지만 새 요약·현재 후보로 자동 이전하지 않는다. `summary_only` 검토는 분석 결과 검토와 구분하고 검토된 추천 판정으로 등록하지 않는다.

로컬 CLI는 검증된 ZIP으로 미검토 `draft` JSON을 만들며 웹은 사람이 완성한 `final` JSON만 등록한다. 작성자·모델·검토 범위는 선언 정보다. 파일 대응 검증, 메타데이터와 맞는 버전, 선언만 있는 버전과 unknown을 구분한다. 코덱스 실행이나 원본 재계산을 서버가 수행한 것으로 표시하지 않고 추천·적용·기기 자격을 승격하지 않는다.

리뷰는 불변 pack 밖의 해당 export job/reviews에 별도로 보관한다. 기존 작성자 소유권과 전체 저장 예산을 재사용하며 revision·이전 리뷰 해시·request ID로 중복 재시도와 충돌을 구분한다. 인증·동일 Origin을 요구하고 리뷰 등록만 128KiB 한도를 사용한다. 기존 제어 요청의 4KiB 한도는 유지한다. 공개 상태는 건수와 revision만, 상세 의견은 인증된 조회로 제공한다.

완료 조건은 정확한 대상 등록·재시작 이력 보존, 다른 묶음/요약/결과 혼합 거부, draft·비정상 JSON·과대 입력·기록 실패·변조·동시 quota 차단, 최신 요약 변경 후 과거 기록 유지다. 이후 현재 제품의 별도 복사본에서 소유 파일·공유 훅·보존 자산을 함께 제거하고 원래 FMS 복원과 기능 재삽입 방지를 확인한다. 현재 checkout과 외부 원본·설정·리뷰 자료는 제거하지 않는다. AGY 회로 중지 상태에서 Sol이 구현·통합하며 CPU 지정·실기기·차량 writer 자격·커밋·푸시는 범위 밖이다.

### 검토 결과 연결 구현·표적 검증

`review_results.py`와 파일/요약/결과 해시 binding, CLI `review-template`, 인증된 등록·조회와 WebCarrot 이력을 연결했다. 초안은 등록하지 않고 final 결과를 원본 pack 밖에 기록한다. 공개 상태에는 검토 건수·최근 revision만 표시한다. 버전 선언은 내보낸 메타데이터와의 일치·선언만 존재·미확인을 구분한다. 동일 요청 재시도는 저장 예산이 거의 찬 상태에서도 무쓰기 성공을 유지하고 새 요청은 예산을 검사한다.

- 저장소 표적 14개, export/CLI 표적 25개, 서버 HTTP 23개, 웹 client/model 63개 PASS. 개수는 서로 다른 검사 묶음이며 전체 회귀 수치로 합산하지 않는다.
- `.analysis/control_advisor_review_results/run-2/e2e-summary.json`의 실제 API 흐름 14조건 PASS: native 형식 생성 입력→비교→주차→완료 export→final 등록→중복/충돌→재시작 이력. fixture writer를 해시 기준 전에 봉인해 cleanup 뒤에도 원본 10개와 완료 pack이 동일하다. run-1은 fixture 종료 기록 추가로 hash가 달랐던 시험 환경 근거로 보존하며 최종 원본불변 근거로 쓰지 않는다.
- 실제 WebCarrot/HTTP 화면에서 로컬 JSON 파일 선택·등록·반복 poll 후 이력·별도 320px 화면 조회를 확인했다. `.analysis/control_advisor_review_results/review-record-desktop.png`, `review-record-details.png`, `review-record-320.png`가 화면 근거다. 검토자는 `test-fixture / generated-fixture`이며 실제 Codex 주행 검토·실차 시험이 아니다. 브라우저와 소유한 localhost 서버를 정리했다.

### 실제 기기 사용 준비로 범위 확대 — 최신 사용자 지시

사용자는 PC 시험용 고정 차단 때문에 차와 PC 사이에서 수정을 반복하지 않도록 실제 사용 경로를 완성하라고 요청했다. 이에 기존 vehicle 무조건 보류·PC 저장소 전용 구조, 환경변수 수동 준비, 관측 담당 수명, 미구현 계산 backend를 분리하여 작업한다. 실기기 미검증이라는 표시는 유지하지만 그 표시만으로 모든 기능을 일괄 차단하지 않는다. 오래된 후보·다른 설정·불명확한 반영 등 실제 조건은 계속 개별 검사한다. 코어 지정·기기 연결·커밋·푸시 금지는 유지한다.

Astra xhigh의 소스 확인 결과, 현재 47개는 수식 backend 26개·raw 진단 2개·미구현 backend 14개·맥락 항목 4개·현 제어식상 무효 1개다. 정책 매핑 22개에는 독립적인 사용자 목표가 필요하며 현재 출력이나 예제 숫자를 정답으로 만들지 않는다. 구현 부재를 단순 현장 미검증으로 숨기지 않는다. 먼저 native 기본 경로·실제 Params 조건부 쓰기·백업/명시 원복·관측 수명·웹 준비 상태를 완성하고, 식별·재생 계산은 근거 조건에 따라 연결한다. 브랜치 교체는 저장된 Params 원복과 다르므로 코드 밖 기준 snapshot과 선택 복구가 필요하다.

사용자의 추가 AGY 사용 요청에 따라 기존 중지 기록을 재확인했다. 과거 run은 `forbidden_tool_attempt / CODEX_READY`이며 manager/child가 종료됐고, 현재 전역 circuit 파일과 활성 공유 소유자는 없다. 정책을 바꾸거나 실패 run을 재호출하지 않고 확정된 별도 runtime 경로 모듈을 새 AGY 작업으로 발행했다. 외부 전달은 선택한 일반 소스뿐이며 원본 주행 로그·비밀값은 포함하지 않는다. 이후 확정된 구현 제안은 AGY 우선, Sol은 실제 diff 검토·통합·시험을 담당한다. 최종 보존 capture와 격리 제거 리허설은 이 확대 범위의 freeze 뒤 한 번 수행한다.

### 실제 사용 기반: AGY 제안 적용과 PC 검증

- `runtime_config.py`는 실제 주입된 Params 경로·기존 로그 경로·native 기본 데이터 위치와 명시 override를 부작용 없이 판정한다. 프로파일/목표 미설정은 분석 항목에 표시하며 수집 전체를 차단하지 않는다. AGY 원제안을 공식 적용한 뒤 fallback·경로·링크 경계를 보완했다. 실제 Windows junction을 포함한 최종 21개 시험 PASS/skip 0이며 `.analysis/control_advisor_vehicle/runtime-config/validation.json`에 근거가 있다. 아직 이것만으로 서버 자동 준비가 연결된 것은 아니다.
- native Params에 `get_raw`, `get_many_raw`, `compare_and_set_raw`를 추가했다. 파일 부재와 빈 bytes를 구분하고 기존 `.lock` 안에서 기대 raw snapshot 전체와 대조한다. 성공·충돌·변경 전 실패·변경 후 내구성 불확실을 구분하며 기존 typed API는 보존한다. AGY 제안을 적용한 뒤 FIFO 차단·엄격 bytes 타입·오류 처리를 보완했다. 실제 WSL 확장 시험 22개(신규 CAS 9+기존 typed 13), C++ 직접 검사 9개, syscall 검사 10개와 기존 writer의 별도 프로세스 경합 PASS. 결과는 `.analysis/control_advisor_vehicle/native-toolchain/native-cas-result.json`이다.
- Linux 시험 도구는 WSL의 사용자 경로 `/root/.local/share/codex-tools/params-native-jammy`에 공식 Ubuntu 패키지를 풀어 구성했다. 시스템 패키지 DB·daemon·OS 설정은 변경하지 않았다. 버전·해시·재현 명령은 같은 `native-toolchain` 폴더의 inventory와 README에 있다. 실제 Params 구현과 실제 생성 스키마/pyx를 컴파일했으며 logging만 시험 대체물이다. Params/CAS 대체 저장소 통과로 네이티브 검증을 대신하지 않았다.

CAS 성공은 consumer 반영이나 실차 자격을 의미하지 않는다. 네이티브 Params와 요청 원장의 원자적 결합·ABA 검출·전원 손실·실차 반응은 보장하지 않으며, 저장 후 원장 기록 전 종료를 현재값 일치만으로 복구하지 않는다. 다음 연결은 외부 기준 백업·명시 원복·실제 vehicle 요청·native QR 연결·서버/관측 수명이다. 이 연결 전에는 실제 사용 준비가 끝났다고 표시하지 않는다.



## 네이티브 연결 및 실사용 보완 — 2026-10-25 후속

사용자는 시험용이라는 이유의 일괄 제한을 피하고 차량에서 다시 코드를 고치는 일을 줄이도록 요청했다. 실제 신호·현재 설정·후보 근거가 충족되면 명시적 적용 요청으로 native Params에 저장하도록 연결했다. 실기기 검증 여부를 저장 가능 여부와 혼동하지 않는다. 실차 성능·부하 검증은 여전히 미실행이다.

- 기기에서는 기존 WebCarrot QR에 일회성 접속 권한을 넣고 같은 브라우저의 HttpOnly 쿠키로 이어간다. URL fragment는 사용 후 제거하며 기존 수동 토큰 경로도 유지한다. QR가 발급되지 않으면 기존 일반 웹 주소를 유지한다.
- 실제 CarParams의 구성·스키마와 물리 차량 기준을 원본·후보·현재 상태에 연결한다. VIN 미확인 차량의 새 부팅에서 기존 기준 또는 새 차량을 직접 선택한다. 다른 차량의 최초 백업을 조용히 재사용하거나 덮어쓰지 않는다.
- 47개 관련 설정의 최초 원시 바이트를 보존한다. 저장은 실제 Params 잠금 안에서 기대값·CarParams를 함께 비교한다. 요청 재시도는 중복 쓰기를 하지 않으며 저장 성공과 제어기 소비 확인을 구분한다.
- 주행 적용 전에는 해당 축의 유효·활성·비개입 상태 2초 연속 관측과 신선한 소비 관측을 확인한다. 개입·신호 공백·종료 시 준비 상태가 해제된다. 주차 관측과 실제 offroad는 별도로 취급한다.
- 분석 종료 뒤에도 해당 적용 요청의 원복을 명시적으로 요청할 수 있다. 이 경로는 실제 IsOffroad/IsOnroad와 현재 CarParams/설정을 조건부 쓰기 시점에 다시 비교하고, 저장 뒤 ‘다음 시동 반영 대기’로 표시한다. 브랜치 변경 자체는 설정 원복이 아니다.

검증 근거: 실제 컴파일된 Params와 실제 Cap'n Proto CarParams를 사용한 WSL 통합 13조건 PASS(일반 Params.put 경쟁·CarParams 변경·offroad 전환 경쟁·종료 후 정확한 바이트 원복 포함). 관련 수집/consumer/실제 production hook/주차 15모듈 123시험 PASS, 마지막 전환 조건 보완 뒤 표적 7시험 PASS. 기존 Python 전체 471시험은 그 이전 기준이며 최신 변경 전체를 검증한 수치로 재사용하지 않는다. HTTP/웹 관련 표적 검사는 별도 근거로 관리한다.

AGY는 runtime 설정과 native CAS 제안에 실제 사용했고 Sol이 통합·검증했다. native store 응답 크기 오류와 native access 허용 외 도구 요청은 유효 제안으로 세지 않았으며 같은 호출을 자동 재생성하지 않았다. 사용자 추가 요청에 따라 별개의 로그 분할 저장 코덱을 AGY에 맡긴다.

남은 구현: 기존 단일 settings journal의 소비 관측 5,000건/4MiB 한도가 장시간 수집을 막는 문제를 분할 저장과 제한된 시간 창 읽기로 해결 중이다. 새 형식은 기존 원본·전역 순번·체인·세션을 보존하며 reader/export까지 연결하기 전 활성화하지 않는다. 종전 471시험 및 기존 제거 리허설만으로 이 통합이 끝났다고 표시하지 않는다. 숫자 계산 백엔드가 없는 항목과 목적 곡선/분석 profile 미설정은 실기기 미검증과 별개의 실제 미완료 조건이다.


### WebCarrot 실제 브라우저 경로 확인

PC의 생성 로그·가짜 Params 저장소를 실제 NativeRuntime/후보 resolver/서버/웹 코드에 연결해 QR 권한 수령 → 후보 저장 요청 → 직전 값 원복을 실행했다. 브라우저에 원복 저장 확인이 표시됐고 종료 기록에서 CAS 2회 및 CruiseMaxVals0 `100 → 130 → 100`을 확인했다. 320px 화면도 확인했다. 이는 실제 차량의 Params 쓰기나 실제 주행 검증이 아니다. 최초 검증 하네스의 비교 provider 형식 오류는 정상 비교 평가기의 보류 결과를 사용하도록 고쳤고 제품의 검증 조건을 약화하지 않았다.

이 과정에서 native 저장 가능한 후보에도 남아 있던 ‘설정 적용 비활성’, 중복 비활성 버튼, native 영수증을 ‘PC 모의 저장’으로 표시하던 문구를 고쳤다. 실제 저장 경로와 소비 확인 대기를 구분한다. 테스트 서버와 브라우저는 종료했고 실제 기기·방화벽·브랜치는 변경하지 않았다.

장시간 기록의 새 AGY 호출은 `c84478734e4146a79f52e560a7a1ad27`이며 응답 이벤트에서 요청한 `gemini-3.8-flash-medium`을 확인했다. 이 기록 시점에는 코드 제안 생성 중으로, 생성 성공·검토·적용·제품 검증을 완료했다고 표시하지 않는다.


### AGY 반환 형식 복구와 작업 배분

위 AGY run은 `invalid_partial_or_conflicting_response`로 종료됐다. 상세 확인에서 완전한 동일 2파일 JSON이 네 번 연결되고 정규식의 JSON 역슬래시 escape가 잘못된 것이 원인이었다. 네 제안의 파일 해시는 동일했고 정형 복원한 Python 구문은 통과했다. 공식 apply 성공으로 처리하지 않고, 반환 코드를 Sol이 검토·선별 적용하는 자료로 재사용했다. 같은 모델 요청은 재생성하지 않았고 전역 거부 정책도 변경하지 않았다. 코덱 적용 후 제품 검증 결과는 별도로 기록한다.

사용자 최신 AGENTS에 따라 새 기능 작성·확장·상당한 후속 수정도 AGY를 우선한다. 이번 writer/window/snapshot/export 연결은 지침 갱신 당시 이미 작성된 통합 코드이므로 다시 생성시키지 않고 계약 보완·검증을 이어간다. 아직 작성하지 않은 수치 추천 입력 연결은 별도 확정 계약으로 AGY에 요청한다. `requires_sol`을 일괄적인 AGY 제외 사유로 사용하지 않는다.


### 후속 수치 추천의 실제 미완료 범위와 이번 마무리 경계

사용자가 작업 시간 증가를 지적하여 새 수치 기능 확장을 착수하지 않고 현재 네이티브 연결·장시간 journal·기존 내보내기의 통합 검증을 먼저 마무리한다. 이는 전체 상세계획 완료 선언이 아니다. Astra의 읽기전용 확인에서 log_signals가 livePose/liveCalibration/liveDelay를 받지 않고 log_candidates의 자동 목적 입력이 policy_curve에 한정되어, Factor·조향비의 계산식이 있어도 독립 실측 반응과 자동 연결되지 않은 것을 확인했다. profile/objective 미설정도 단순 현장 검증 문제가 아니라 기능 연결 미완료다.

다음 승인 범위의 구현 순서는 순수 lateral observation 어댑터를 AGY로 작성하고 기존 로그 서비스·측정 참조 목적·fit 절편·기본 분석 설정에 연결하는 것이다. source CP, 보정 yaw, 실제 지연 정렬 및 실제 선형 callback 사용을 확인해야 하며 filtered torque offset을 실제 사용값으로 가정하지 않는다. 22개 정책은 사용자 목적이나 명시한 정책 목표가 필요하다. PID gain·지연·smoothing·VEgoStopping·MPC cost의 실제 추천은 식별 모델과 원본 제어기 재현 연결이 별도로 남아 있다. 과거 오토튜너 증감량은 검색 후보 씨앗이며 정답이나 안전 한도로 간주하지 않는다. 코어 배치/콤마 부하/실차 개선 검증과 이 코드 미완료 범위를 구분한다.

### 분할 기록 통합 동결 및 검증 근거

실시간 worker 생성 지점에서만 segmented journal을 활성화했다. 기존 v1 읽기와 라이브러리 기본값은 유지한다. 소비 관측 5,101건과 다중 회전 뒤에도 전역 순번·원본 해시·과거 checkpoint가 유지되며, 분석에는 필요한 시간 창과 참조 의존 기록만 선택한다. 선택 범위 검증을 전체 세션 검증으로 표시하지 않는다. 명시한 저장 공간 예산은 유지하며 기존 원본을 자동 삭제하지 않는다. 기본 1시간 세션 종료 제한도 제거했다.

통합 과정에서 v2의 없는 단일 settings.jsonl을 검사하던 None pin 오류와 source-only inspection의 누락된 records 요청을 수정했다. 원래 검증 조건을 유지한 추천→조건부 저장→전후 비교→과거 snapshot 재검증 14시험, 선택 기록만 남긴 독립 ZIP 검사, 주차 요약·요청 원장의 독립 ZIP 검사에 각각 통과했다. 코덱은 Windows 16시험 중 OS 조건부 1개 제외, POSIX 16시험 전체 통과했다. 5,101건 관측을 포함한 수집·기록 관련 56시험도 통과했다.

최종 전체 회귀의 실제 실행 결과는 `.analysis/control_advisor_vehicle/final-advisor-tests.txt`, 보존·격리 제거 리허설은 외부 `preservation_review_results_worker/removal_rehearsal/removal_result.json`으로 확인한다. 이 문서 동결 시 해당 두 실행은 별도 진행 중이며 완료·성공 판정은 각 결과 파일을 따른다. 앞선 표적 PASS를 전체 회귀 성공으로 확대하지 않는다. 커밋·푸시·실기기 변경은 수행하지 않았다.

## 실기기 확인과 오토튜너 실행 배치 후속

실제 콤마의 현재 WIP 실행 버전과 같은 기준 HEAD를 확인했고, Tailscale로 PC→파이→콤마의 기존 WebCarrot API에 직접 접근했다. 폰 화면 조작·별도 폰 앱 없이 25초 동안 9개 서비스의 선택 수치 4,283건을 받았다. 표본의 메시지 valid는 모두 참이었지만 당시 P 기어·제어 비활성 상태였으므로 실제 추종이나 원격 전체 원본 보존을 입증하지 않는다. 기존 기기의 설정·브랜치·CPU 배치는 변경하지 않았다.

스레드별 affinity와 `/proc/stat` 20초 측정으로 **새 오토튜너 작업자는 CPU 0**을 선정했다. CPU 0 평균 72.5%, 최대 77.8%; CPU 1 센서, 3 CAN, 4~7 planner/card/control/model의 전용 작업을 피했다. 낮은 사용률의 CPU 7을 모델과 공유하지 않는다. 이는 설치 후 오토튜너 ON/OFF 비교를 시작하기 위한 배치 결정이며, 해당 기능의 실제 주행 부하 검증은 남아 있다.

`worker_runtime.py`가 native live 작업자의 시작 시점에 CPU 0·SCHED_OTHER·nice 최소 15를 적용한다. 기존 nice가 더 크면 유지한다. 모든 새 스레드·RecordingSession·NativeTransport 생성 전에 실행하고, 별도 분석 자식은 OS 배치를 상속한다. 부모 웹 서버나 기존 제어 프로세스는 변경하지 않는다. PC/합성은 배치를 변경하지 않으며 실패 시 요청값·실효값·고정 오류 코드를 `metadata.worker_runtime`에 기록한다. 부하 보호 기능이나 추천 계산기 전체가 완성됐다는 의미는 아니다.

Windows 표적 55개 중 Linux 전용 1개 제외하고 통과, WSL runtime/integration 17개 전부 통과했다. 실제 격리 자식의 CPU 0·SCHED_OTHER·nice 15와 손자의 동일 상속, 시험 부모의 배치 불변을 커널 syscall로 확인했다. native 감지만 시험 지정했으며 실기기에 이 변경을 설치하지 않았다. AGY 실행 `d61885f0b4174c5e86a4d2471567f29a`는 생성 도중 중단되어 공식 apply 성공으로 세지 않았다. 완성된 반환 모듈과 시험 일부를 Sol이 검토·보완했고 같은 요청은 재생성하지 않았다.

사용자가 로컬 수정·계획 갱신 후 GitHub `fms-carrot-wip-auto` 게시를 승인했다. [설치·주행 검증 안내](fms_control_advisor_install_test.md)에 기기 반영 순서와 아직 남은 기본 profile/목적·실제 반응 신호·항목별 추천 계산 연결을 명시했다. 이전 ‘커밋·푸시 금지’는 auto 브랜치 게시에 한해 대체되며 기존 WIP·안정 브랜치와 기기 실행 상태는 유지한다.

게시 전 최종 회귀의 실제 결과는 `.analysis/control_advisor_vehicle/publish-advisor-tests.txt`, 보존 갱신 결과는 외부 `preservation_runtime_worker`의 검증 요약을 따른다. 이 문서 동결 시 두 실행은 아직 진행 전/진행 중이며, 이전 567시험이나 위 표적 PASS를 새 전체 회귀로 표시하지 않는다.
