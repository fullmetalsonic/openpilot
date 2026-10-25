# FMS 제어 분석: 오프라인 진단·실시간 수신·WebCarrot

기기 사용은 [설치·주행 검증 안내](../../../../docs/fms_control_advisor_install_test.md), 다음 구현 순서는 [경량화·실차 완성 기획안](../../../../docs/fms_control_advisor_completion_plan.md)을 따른다. 아래 날짜별 개발 단계의 제한은 최신 기기 경로와 구분한다.

현재는 **진단·원본 보존·소비 지점 관측·주기 후보 계산·네이티브 설정 저장·원복과 전후 비교를 연결한 개발 단계**다. WebCarrot 로그 화면의 `제어 분석` 버튼에서 상태를 확인한다. 실시간 작업자는 명시적으로 시작해야 실행된다. 후보의 근거와 현재 차량 조건을 확인한 명시적 요청만 Params 저장으로 연결한다. 모듈 import나 오프라인 CLI 실행은 차량에 접속하지 않는다. 자동 수치 추천의 일부 백엔드·신호 연결과 실차 검증은 아직 남아 있다.

## 구현된 범위

- 수치 보정 대상 43개와 맥락 설정 4개의 단위·소비 경로·특수값·의존성 목록. `CustomSR`은 기존 수동 모드 안에서만 수치 보정을 다루며 메뉴 범위는 검증된 변경 한도가 아니다.
- 정규화 JSONL 또는 raw/zstd/bzip2 로그를 읽고 축·설정 구간별 편향, RMSE, 절대오차 95백분위, 관측/사용 시간, 제외 사유를 계산한다.
- 개입 직전·개입 중·해제 후 회복, 신호 공백/무효/미관측, 출력 제한, 의존 창을 구분한다. 큰 추종 오차 자체는 제외하지 않는다. 최근 꼬리 자료는 잠정으로 남긴다.
- 선택한 입력과 프로파일을 새 결과 폴더로 복사하고 분석 전 해시와 대조한다. 원본은 수정하지 않는다. 복사·검증 실패나 취소 시 완료 manifest를 만들지 않는다.
- 기존 torqued의 TLS 계산을 별도 순수 어댑터로 재사용한다. PC 후보 엔진의 Factor 보정과 마찰 잔차 진단에도 사용하며, 차량용 추천·적용 자격을 만들지는 않는다.
- 47개 설정 항목을 10개 소비 지점에서 읽기·사용·생성자 고정·우회로 구분해 관측한다. 생산 파일 9개의 제한된 관측 훅이며 전체 반영 완료 ACK가 아니다.

목록의 정적 준비 상태와 항목별 실제 계산 가능 여부, 현재 요청의 적용 자격은 별개다. 차량 검증은 여전히 `not_validated`이며 계산식이나 저장 경로의 존재만으로 실차 추천을 검증 완료로 표시하지 않는다. 항목별 수식·입력·백엔드의 현재 근거를 표시하며 첫 버전이라는 이유로 항목을 숨기지 않는다. 오프라인 CLI 자체는 차량 설정을 적용하지 않는다.

## PC에서 직접 실행

저장소 루트에서 실행한다. JSONL·파라미터 목록·증거 검증은 Python 3.10 이상 표준 라이브러리만 사용한다. 아래 예제는 실제 주행과 무관한 **합성 자료**이며 프로파일의 시간·한도도 실차 권장값이 아니다.

```powershell
python -m openpilot.selfdrive.carrot.control_advisor inventory

$demoOutput = Join-Path $env:TEMP ("fms-control-advisor-" + [guid]::NewGuid().ToString("N"))
python -m openpilot.selfdrive.carrot.control_advisor analyze-jsonl `
  openpilot/selfdrive/carrot/control_advisor/examples/synthetic.jsonl `
  --profile openpilot/selfdrive/carrot/control_advisor/examples/host_profile.json `
  --output $demoOutput

python -m openpilot.selfdrive.carrot.control_advisor verify $demoOutput
```

기대 결과: 첫 명령은 47개 항목의 준비 상태를 출력한다. 분석은 `state: complete`, `scope: offline_diagnostic`, `apply_enabled: false`를 출력한다. 이는 **증거 묶음 작성 완료**이며 튜닝·검증 완료라는 뜻이 아니다. 같은 출력 경로를 재사용하면 덮어쓰지 않고 실패한다.

묶음의 `result.json`에서 `diagnostic.axes.lateral.epochs.synthetic-A`와 longitudinal을 확인한다. `raw_metrics`에는 제외·잠정 구간의 유한한 측정값도 포함한다. `eligible_metrics`는 선언한 진단용 규칙으로 확정된 구간의 통계다. 이것만으로 튜닝 자격이 생기지 않는다. 두 축의 오차는 모두 `actual - target`이며 현재 어댑터의 물리 단위는 m/s²다. JSONL을 다른 단위로 작성하면 그 의미를 별도로 관리해야 한다.

`excluded_count + provisional_count + eligible_count = sample_count`다. 제외 사유들은 겹칠 수 있어 사유별 개수의 합은 제외 개수와 같지 않을 수 있다. 유효시간은 양 끝이 확정·사용 가능한 동일 구간의 연속 관측 사이만 합산하며, 누락 시간을 보간하지 않는다.

동봉 예제의 실제 확인값은 축마다 총 16개 중 사용 5개·제외 9개·잠정 2개, 유효시간 0.3초다. 사용 구간에 넣은 큰 오차도 그대로 남아 RMSE는 약 0.898888m/s²다. 이 숫자는 시험 입력의 기대 결과이며 차량 성능이나 권장 설정값이 아니다.

## 실제 로그 입력

이 경로는 선택적 `pycapnp`, zstandard 로그에는 `zstandard`가 필요하다. 현재 PC에서는 기존 CAN 분석 도구의 격리 Python 환경으로 검증했다. 새 전역 패키지나 장치 소프트웨어는 설치하지 않았다. 제품 의존성 선언은 `pyproject.toml`에 이미 있다.

이번 native 시험 환경은 Python 3.12.10 / pycapnp 2.2.1 / zstandard 0.25.0이다. 제품 선언은 pycapnp 2.1.0이고 2.2의 cyclic reference 메모리 누수 주의가 있으므로, 이 PC 시험을 제품 의존성 일치나 장시간 메모리 검증으로 확대하지 않는다. 기존 환경을 재사용했으며 제품 의존성은 변경하지 않았다.

다음 명령의 `python`은 해당 의존성이 있는 환경이어야 한다. 닫힌 세그먼트를 직접 선택하고, 개입/회복/신선도 프로파일도 명시해야 한다. `--epoch`는 사용자가 붙이는 분석 구간 식별자이며 설정 이력이 완전하다는 증거가 아니다.

```powershell
python -m openpilot.selfdrive.carrot.control_advisor analyze-rlog C:/logs/rlog.zst `
  --log-kind rlog --epoch recording-segment-A `
  --profile C:/analysis/diagnostic-profile.json --output C:/analysis/new-result
```

- qlog라면 `--log-kind qlog`로 표시한다. 샘플링이 축약된 qlog는 정밀 튜닝 근거로 취급하지 않는다. 생성한 시험 로그는 `--synthetic`도 지정한다.
- 현재 decoder schema와 기록 schema 일치는 검증되지 않았다고 기록한다. 과거 schema의 기본 0값을 실측 신호의 존재 증거로 사용하지 않는다.
- carState 시각마다 두 축을 만든다. 최신 과거 값만 제한된 나이 안에서 연결하며 각 목표·반응의 원시 시각을 남긴다. 미래·오래된 값은 유효값으로 사용하지 않는다. 이 방식은 짧은 페달/조향 개입을 보존하지만, 횡방향 반복 유지값을 독립 관측으로 만들어 주지는 않는다.
- 종방향 목표는 `longitudinalPlan.aTarget`이며 `accels[0]`으로 대체하지 않는다. 횡 반응은 torqueState의 차량 모델 유래 값이다. 독립 yaw/roll 기반 반응 검증이나 CAN 송신 요청 복원은 아직 없다.
- 일반 정차와 SoftHold/차량 hold를 구분한다. 실제 consumer 재초기화·잔류 반응 확인이 없는 회복 판정은 `state_only_unvalidated`로 표시한다.
- 한 번에 선택한 파일 하나를 처리한다. sample/event/해제 바이트 한도를 넘으면 조용히 잘라 성공시키지 않고 실패한다. 현재 관측 배열은 제한된 크기로 메모리에 유지하므로 상시 구독용 스트리밍 엔진이 아니다. zstd 완결성 검사는 압축 파일을 추가로 한 번 읽는다.

## 입력·보존 계약

JSONL 첫 줄은 `type=session`, `schema_version=1`, `source_kind=synthetic|normalized_rlog|qlog|live_capture`인 객체다. 이후 각 줄은 `Observation`의 필드다. `mono_ns`는 축별 엄격히 증가해야 한다. 각 설정 변경에는 새 `epoch` ID를 부여한다. liveDelay·GAP 같은 동적 맥락 변화만으로 새 설정 구간을 만들지 않는다. 소스가 이미 순서가 뒤섞였거나 설정 이력이 불명확하면 이 도구가 이를 복구했다고 간주하지 않는다.

프로파일은 모든 시간 기준을 명시하고 `validation_status`를 `synthetic_only` 또는 `not_validated`로 둔다. 현재 CLI는 검증 완료를 자칭하는 프로파일을 받지 않는다. 시간은 ns이며 1초=1,000,000,000ns다. 기본 파일/복사 예산 512MiB, 이벤트 최대 8MiB, 이벤트 수 200,000, 관측 상한은 프로파일에서 지정한다. 이 값들은 PC 작업 상한이며 콤마의 검증된 자원 예산이 아니다.

출력에는 입력·프로파일·분석 코드·decoder schema 해시를 남긴다. `bundle.json`은 복사본과 `result.json`을 검증하는 완료 표식이다. 원본이 분석 후 바뀌거나 파일/공간 오류가 나면 완료 표식 없이 중단된다. 불완전 폴더는 원본과 구분해 남기며 자동 삭제하지 않는다. Windows에서는 파일 fsync까지만 수행하므로 Linux directory fsync와 같은 전원 손실 내구성을 주장하지 않는다.

**보존된 원본은 자동 비식별화하지 않는다.** 결과의 설정 목록은 allowlist로 제한하지만 묶음에 복사한 원본에는 차량·개인 식별정보가 있을 수 있다. 외부 전송은 이 도구가 하지 않는다. 사람이 검토하여 선택한 묶음만 이후 Codex 2차 검토에 전달한다.

## 순수 토크 특성 추정 어댑터

`torque_fit.estimate_torque(points, max_input_points=..., max_fit_points=..., seed=...)`는 시간 정렬·개입 제외·독립 횡가속도 준비가 끝난 `(torqued 좌표의 조향 명령, 횡가속도)` 쌍만 받는다. x의 부호는 원본 torqued처럼 `-carOutput.actuatorsOutput.torque`이며 실제 모터 토크 N·m가 아니다. 최대 토크·EPS/Panda 한도를 계산하거나 변경하지 않는다.

원본 `slope2rot`·TLS SVD·마찰 spread 수식과 AST 지문을 시험으로 대조한다. Params 읽기/쓰기/삭제, 학습 캐시, TorqueEstimator 생성 없이 계산하며 seed와 선택 표본 인덱스를 기록한다. 원시 결과를 CP 범위로 clip하거나 필터링하지 않는다. 수치 퇴화·부족 자료·라이브러리 부재는 unavailable로 반환한다. `recommendation=null`, `apply_enabled=false`, `profile_qualified=false`다.

현재 rlog 진단의 모델 유래 횡반응을 이 어댑터에 자동으로 넣지 않는다. 독립 반응·좌우/속도 분포·FF/Kf 식별·확인 블록 검증이 먼저 필요하다. 개발 시험은 공용 Python 3.12.14 / NumPy 2.3.5에서 수행했으며 제품 NumPy 선언 버전의 검증으로 확대하지 않는다.

## 검증과 남은 단계

```powershell
# pycapnp/zstandard/NumPy가 있는 Python: native decoder 포함 전체 영향 범위
python -m unittest discover -s openpilot/selfdrive/carrot/control_advisor/tests -p "test_*.py" -q

# 표준 Python: 합성 3분/5분 관측의 host 계산 크기 확인
python -m openpilot.selfdrive.carrot.control_advisor.tests.benchmark_host
```

오프라인 시험은 합성 자료, 실제 Cap'n Proto 및 압축 라이브러리, 실제 CLI subprocess로 수행한다. 추가 실시간 시험은 합성 입력과 실제 작업자·HTTP·브라우저 연결을 구분하여 검증한다. 실주행 자료·기기의 native IPC·Params·실제 ECU 검증은 아니다. benchmark는 Python 할당 메모리와 계측을 포함한 처리시간이며 RSS·콤마 부하·추정 알고리즘 비용이 아니다.

초기 오프라인 단계의 2026-10-25 당시 host 영향 묶음은 75개 PASS(2.247초)였다. 현재 소비 관측 단계의 총 시험 수를 뜻하지 않는다. 당시 기존 CAN 분석 venv의 CPython 3.12.10 / pycapnp 2.2.1 / zstandard 0.25.0에 공용 CPython 3.12용 NumPy 2.3.5의 검색 경로를 **해당 시험 프로세스에서만** 추가해 실행했다. 당시 설치·전역 환경·제품 lockfile 변경은 없었다. 순수 수식 8개는 공용 Python 3.12.14에서도 별도로 통과했다. 후속 단계의 현재 검증 결과는 `docs/fms_control_advisor_stage0.md`에 기록한다.

저장 설정 sidecar·완료 원본 보존과 소비 지점의 표본 관측은 아래 기능으로 연결했다. 요청 등록·PC 모의 조건부 쓰기·관측 기반 전후 비교는 아래 단계로 연결했다. 후속 작업은 전체 소비 지점의 반영 경계 검증, 독립 반응/CAN 해석, 후보 엔진의 미구현 백엔드와 실차 검증 프로파일, 차량 쓰기 자격과 실제 적용 전후 검증·주차 판정이다. 기기 코어 지정·실측은 구현 뒤 배포 전 관문이다. 개발 순서를 파라미터군의 영구 지원 제한으로 바꾸지 않는다.

## 비활성·제거 경계

환경의 `CARROT_CONTROL_ADVISOR_LIVE_ENABLED=0`·`CARROT_CONTROL_ADVISOR_ALLOW_DEMO=0`으로 해당 새 작업자 시작을 차단할 수 있다. 이미 시작한 세션은 중지하거나 웹서버를 정상 종료한다. 상태 조회와 화면 열기는 작업자를 시작하지 않는다. 소비 지점 훅은 native 기본 경로 또는 아래 환경변수로 제어하며 명시적 OFF를 우선한다. manager 시작 정책·기존 제어 출력 수식은 유지하고 설정 저장은 별도의 명시적 요청으로 수행한다.

자동 후보 계산만 끄려면 `CARROT_CONTROL_ADVISOR_AUTO_ENABLED`를 해제하고 현재 수집을 종료한 뒤 다음 수집부터 적용한다. 기존 수집·원본 보존 기능은 별도 설정이다. 웹의 자동 결과 조회도 끄려면 `CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT`를 해제하고 웹서버를 정상 재시작한다. 이미 저장된 원본·분석 결과를 삭제하는 동작은 없다.

공유 소스 접점은 `server/features/__init__.py`의 import/등록, `web/src/entries/logs.js`·`logs.css`의 각 import, 아래 표에 있는 생산 파일 9개의 관측 훅이다. 제거할 때 새 수집을 중지하고 이 접점과 전용 observer 호출·읽기 시각 추적 부분, 전용 서버/웹 feature·시험, 이 폴더와 문서를 제거한다. 기존 제어·계획 로직과 FMS 계약은 보존하며 과거 파일 전체로 되돌리지 않는다. 이어서 웹 번들·asset manifest와 FMS 재구성 manifest·해당 preservation 자산을 함께 재생성·검증해야 다음 업데이트에서 기능이 되살아나지 않는다. 기존 FMS patch 전체를 삭제하지 않는다. 코드 제거, 튜닝 설정 원복, 외부 원본/결과 삭제는 별개다. 기본 제거는 기존 설정과 증거를 보존한다.

## 실시간 화면과 작업자

WebCarrot의 **로그 → 제어 분석**에서 `실시간 추천 / 적용 전후 비교 / 주차 요약`을 연다. 기존 대시캠·화면녹화 탭은 그대로다. 기록 기능이 연결되면 47개 항목의 관측 저장값을 표시하며 읽지 못한 값은 `—`다. 별도로 계산해 불러온 오프라인 후보는 아래 절의 미검증 상태로 표시한다. 일반 실시간 수집만으로 숫자 후보를 자동 생성하지는 않는다. native 기기는 현재 차량·설정·후보·상태가 검증된 명시적 요청을 저장하고, PC 모의 저장은 별도로 표시한다. 저장 이후 소비 관측과 비교를 기다린다. 세션 종료는 주차 판정이 아니며 주차 탭은 부족한 근거를 표시한다.

아래는 초기 PC 개발 세션용 명시적 환경 설정이다. 실제 native 기기는 경로·QR 권한·관측을 자동 구성하며 위 설치 안내를 따른다. native에서 환경 설정이 필요 없다는 사실이 미구현 추천 계산기까지 활성화한다는 뜻은 아니다.

- `CARROT_CONTROL_ADVISOR_TOKEN`: 공백 없는 ASCII 32–256자 세션 제어 키. 별도로 생성한 값을 화면에 입력한다. 화면은 메모리에서만 사용하며 URL·localStorage에 저장하지 않는다. 서버는 작업자에게 이 키를 전달하지 않는다.
- `CARROT_CONTROL_ADVISOR_ALLOW_DEMO=1`: 명시적인 합성 데모 시작을 허용한다. 600초의 논리 시간을 약 10초로 가속하며 화면에서 계속 합성임을 표시한다. 3/5분의 실제 주행 관측이 아니다.
- `CARROT_CONTROL_ADVISOR_LIVE_ENABLED=1`: 실제 native 메시지 수신 시작을 허용한다. native 의존성이 없는 PC에서는 사용 불가로 종료하며 합성으로 자동 대체하지 않는다. 실제 기기 사용 승인을 뜻하지 않는다.

화면에서 인증과 3분/5분 주기를 선택한 뒤 원하는 모드를 시작한다. 브라우저는 같은 Origin의 인증된 POST만 사용한다. 한 웹서버에 작업자는 최대 하나이며 여러 탭의 중복 시작을 거부한다. 화면을 닫으면 폴링만 멈추고 작업자는 계속된다. 중지 버튼·웹서버 종료·부모 파이프 종료는 작업자를 정리한다. 기본 1시간 세션 상한은 제거됐다. 화면의 `중지`는 수집·분석 작업자를 종료한다.

작업자는 carState/carControl/controlsState/longitudinalPlan을 최신값 축약 없이 구독하고, 시각 정렬 후 오프라인과 같은 정규화·개입 마스크를 사용한다. 이벤트 1MiB, 정렬 큐 4,096개/8MiB, 관측 100,000개, 출력 256KiB, 최근 요약 6개가 host 예비 상한이다. poll은 최대 256개/협력적 5ms 예산이며 단일 native decode를 강제 중단할 수 없으므로 초과 횟수를 별도로 기록한다. 늦은 입력·공백·큐 초과는 결과를 무효화하고 새로운 관측 구간을 기다린다. 공백 검출은 시각 기반 추정이며 정확한 IPC 누락 개수를 증명하지 않는다.

기본 5분(선택 3분)마다 계산한다. 마지막 꼬리는 잠정이며 중지 시 부분 요약은 완전한 주기와 구분한다. 현재 host 프로파일은 개입 전 0.1초·회복 2초·의존 0.1초 등 **미검증 진단 기준**이다. 설정 이력·소비 반영의 완전성은 미확인으로 유지하며 이전 구간과 겹치는 계산은 독립적인 확인 블록으로 취급하지 않는다.

실시간 개별 메시지는 유한한 메모리 버퍼다. 기록 기능은 저장값 관측 이력·불변 진단 결과·선택한 완료 rlog 사본을 따로 보존한다. 별도의 상시 raw 로거는 추가하지 않았다. 검증된 개별 사본 수는 전체 주행 원본의 완전한 보존을 뜻하지 않는다. 기존 logger의 원본 기록 경로와 제어 출력은 유지한다. native 오토튜너 작업자와 분석 자식만 CPU 0·SCHED_OTHER·nice 15로 배치하며, PC·합성 실행은 호스트 배치를 변경하지 않는다.

## 설정 이력과 완료 원본 보존 사용

웹서버 실행 환경에서 아래 세 경로를 모두 지정한다. 웹 요청으로 임의 경로를 지정하는 API는 없다. 원본·설정·보존 디렉터리는 서로 겹칠 수 없다.

| 모드 | 설정 파일이 있는 디렉터리 | 기존 logger의 원본 디렉터리 | 새 보존 파일의 상위 디렉터리 |
|---|---|---|---|
| 실제 수신 | `CARROT_CONTROL_ADVISOR_SETTINGS_DIR` | `CARROT_CONTROL_ADVISOR_LOG_ROOT` | `CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT` |
| PC 합성 fixture | `CARROT_CONTROL_ADVISOR_DEMO_SETTINGS_DIR` | `CARROT_CONTROL_ADVISOR_DEMO_LOG_ROOT` | `CARROT_CONTROL_ADVISOR_DEMO_EVIDENCE_ROOT` |

합성 모드는 실제 수신 경로로 대체하지 않는다. 예를 들어 PC 시험에서는 각각 `C:/advisor-fixture/settings`, `C:/advisor-fixture/logs`, `C:/advisor-fixture/evidence` 같은 별도 절대 경로를 사용한다. 앞의 두 디렉터리에 실제 시험 파일이 있어야 한다. 임의 텍스트 파일을 rlog로 이름만 바꾸면 검증에서 거부된다. 이 경로 예시는 기기 설치·실주행 실행 지시가 아니다.

화면에서 세션을 시작하면 설정 관측과 원본 복사가 서로 다른 I/O 담당에서 실행된다. 화면을 닫아도 세션은 계속되고 `수집 종료`로 끝낸다. 기록 기능을 지정하지 않은 기존 수신·진단 모드도 유지한다.

- **저장값 관측:** 등록된 47개 키를 읽으며 원문, 읽은 시간 범위, 누락·오류를 기록한다. 값이 없다고 0이나 기본값으로 대체하지 않는다. `CurrentRoute`는 원본 연관성을 위한 별도 보조 관측이며 공개 설정값 목록에 포함하지 않는다. 이 스캔 자체는 소비값·적용 시각의 증거가 아니다. 별도 소비 관측도 아래 범위의 표본이며 전체 적용 ACK로 사용하지 않는다.
- **변경 구간:** 순차 스캔 중 변경이나 관측 공백의 가능성을 기록한다. polling 사이의 변경 후 복귀를 모두 검출한다고 보장하지 않는다. 변경 시 양 축 공용 진단 창을 보수적으로 새로 시작한다. 이 단계의 관측 revision을 실제 설정 적용 epoch나 개선 검증으로 사용하지 않는다.
- **원본 사본:** 진단이 참조한 route와 시각 범위의 완료 세그먼트만 대상으로 한다. route 연관성이 불명확하거나 로그가 열려 있으면 대기한다. lock 부재뿐 아니라 복사본의 압축 EOF·시작/종료 표식·원본/사본 해시를 확인한다. 개입·회복 구간도 사본에서 삭제하지 않는다.
- **실패와 공간:** 큐·파일·용량·읽기 예산·여유 공간 한도를 넘으면 신규 보존을 보류한다. 원본과 기존 완료 자료, 실패한 partial을 자동 삭제하지 않는다. 기록 실패를 설정 적용이나 차량 제어 실패로 처리하지 않는다.

현재 host 한도는 세션당 원본 사본 512MiB/128개, 진단 8MiB/128개, journal 8MiB다. 보존 루트는 누적 4GiB/32세션이며 새 세션에 544MiB 여유를 요구한다. 부분 파일도 포함해 계산하고 OS 잠금으로 같은 루트의 동시 작성자를 제한한다. 설정 작성자가 실패해도 원본 I/O가 끝나기 전에는 잠금을 해제하지 않는다. 이 수치는 기기에서 검증한 저장·부하 예산이 아니다.

보존 작업은 고정 route·연속 세그먼트 번호·시간순·진단 창 양끝 포함과 인접 경계 250ms 이하를 확인한다. 250ms는 기존 host 진단 기준이며 실제 빈 간격을 기록하고 보간하지 않는다. 완료는 선택한 파일들의 보존 작업 완료만 뜻한다. 한 분석 주기 동안 해결되지 않거나 수집이 끝난 작업은 시간 초과·불충분·취소로 이력에 남긴다. 다음 작업 슬롯은 해제하지만 전체 범위 확보로 표시하지 않는다.

보존 디렉터리 아래 새 세션 ID 폴더에는 `recording.json`(모드·시계), `settings.jsonl`(순서/해시를 가진 관측 이력), `diagnostics/`(불변 1차 진단), `archive/`(완료 rlog 사본과 manifest)가 있다. 사본은 이미 기록된 진단·설정 revision과 journal 순번/해시에 연결된다. SHA-256은 변경 검출용이며 신뢰할 수 없는 작성자의 진위를 인증하는 서명이 아니다.

종료 또는 중단 후 다음 명령으로 **읽기 전용** 검사한다. 새 파일을 만들거나 partial을 수리·삭제·완료로 바꾸지 않는다.

```powershell
python -m openpilot.selfdrive.carrot.control_advisor inspect-session C:/advisor-fixture/evidence/SESSION_ID
```

`state: verified`와 종료 코드 0은 확인한 저장 파일·참조의 무결성을 뜻한다. `coverage_complete: false`, `settings_history_complete: false`, `consumer_applied: false`는 그대로 유지된다. 종료 기록 없는 정상 journal prefix, 손상된 진단, 다른 세션/진단 참조, 미완료 사본, 누락된 작업 결과는 `partial` 또는 실패와 종료 코드 2로 표시한다. 완료 작업도 현재 남아 있는 검증된 사본으로 범위를 재계산하므로 사본 소실을 완료 이력만으로 통과시키지 않는다. 검증 가능한 prefix와 원본 파일은 그대로 남는다. 실제 전원 손실·파일시스템 내구성·I/O가 제어 지연에 미치는 영향은 기기에서 별도 확인해야 한다.

## 소비 지점의 읽기·사용값 관측

`consumer_observer.py`의 소유 목록은 47개 설정 키와 아래 10개 소비 지점을 연결한다. 첫 두 지점이 같은 파일에 있으므로 실제 생산 파일은 9개다. 이 목록은 관측 접점의 정적 범위이며 현재 차량·모드의 모든 항목이 활성이라는 뜻이 아니다.

| 소비 지점 | 생산 파일 | 관측 범위 |
|---|---|---|
| `controls.vehicle_model` | `openpilot/selfdrive/controls/controlsd.py` | 차량 모델의 조향비 override·해석값 |
| `controls.lateral_target` | `openpilot/selfdrive/controls/controlsd.py` | 선택된 횡 목표 경로·지연·평활 |
| `controls.torque` | `openpilot/selfdrive/controls/lib/latcontrol_torque.py` | 토크 계수·PID/FF·Custom/NNFF·생성자 고정값 |
| `controls.longitudinal` | `openpilot/selfdrive/controls/lib/longcontrol.py` | 종 PID·FF·정차 가속도·차종 고정 경로 |
| `modeld.action` | `openpilot/selfdrive/modeld/modeld.py` | 모델 action의 지연·평활·정지 기준 |
| `planner.lateral` | `openpilot/selfdrive/controls/lib/lateral_planner.py` | lane MPC 비용·경로 오프셋 |
| `planner.lane_path` | `openpilot/selfdrive/controls/lib/lane_planner_2.py` | lane 경로의 시간축 보정 |
| `planner.longitudinal` | `openpilot/selfdrive/controls/lib/longitudinal_planner.py` | 종 지연·정지 기준·속도별 가속 상한 |
| `planner.carrot` | `openpilot/selfdrive/carrot/carrot_functions.py` | 차간·앞차 응답·정지거리·가속 상한의 정책값 |
| `planner.long_mpc` | `openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py` | MPC가 선택한 차간·앞차 응답·정지거리 맥락 |

기본은 꺼짐이다. 켜려면 `CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS=1`과 `CARROT_CONTROL_ADVISOR_CONSUMER_SOCKET`의 같은 절대 소켓 경로가 **관측할 제어/계획 프로세스와 수집 작업자 양쪽의 실행 환경에** 있어야 한다. 작업자는 웹서버가 가진 환경을 상속한다. 웹서버에만 지정하면 이미 실행 중인 제어 프로세스의 환경이나 생성자에서 꺼진 observer가 자동으로 켜지지 않는다. manager의 환경 전달·기동 정책·재시작은 이번 기능이 변경하지 않는다. 이 설명은 기기 프로세스를 지금 재시작하라는 지시가 아니다.

native 전송은 Linux의 비차단 `AF_UNIX` datagram이다. 소켓의 상위 디렉터리는 같은 사용자 소유의 private 디렉터리여야 하며 group/other 권한이 없어야 한다. 링크 경로와 기존 소켓 경로는 거절하며 수집기가 만든 소켓은 0600으로 제한한다. 현재 Windows PC에서는 native 경로를 사용 불가로 표시한다. UDP·네트워크 전송이나 합성 입력으로 자동 대체하지 않는다. 합성 consumer transport는 `source_mode=synthetic`인 명시적 시험 주입에서만 허용하며, 일반 합성 메시지 재생이 실제 소비 훅을 검증했다고 표시하지 않는다.

작업자가 비정상 종료되거나 SIGKILL을 받으면 고정 경로의 소켓이 남을 수 있다. 기존 경로는 `consumer_socket_already_exists`로 거절하며 자동 삭제·이름 변경·소유권 추정 복구는 지원하지 않는다. 복구 조건은 수집기 종료를 먼저 확인하고, 새로 소유한 private 상위 디렉터리와 새로운 절대 소켓 경로를 정한 뒤 소비 프로세스와 수집기 양쪽에 같은 환경을 적용해 재시작하는 것이다. 기존 경로를 지우지 않으며 실제 Linux의 이 복구 흐름은 미검증이다. 기기의 비주행 재시작은 별도 승인과 검증이 필요한 후속 작업이고 이번 PC 구현이 실행 권한을 부여하지 않는다.

opt-in이 꺼져 있으면 observer는 identity·파일·해시·소켓을 조회하지 않는다. 켜져 있을 때 각 지점은 최대 1Hz의 표본을 제한된 primitive/배열로 복사해 유한 큐에 넣으며, JSON·코드 해시·소켓 처리는 프로세스별 별도 전송 스레드가 수행한다. 기본 전송 큐 64개, 이벤트 16KiB, 수집 poll 128개/협력적 2ms, 이벤트·읽기 나이 기준 각 2초, 미래 시각 허용 250ms는 host 예비 한도다. 큐 경합·초과·전송 실패는 관측 누락으로 남기고 제어 루프에서 기다리지 않는다. 매 제어 주기의 무손실 로그가 아니며 누락 개수는 합쳐진 경합 표시를 포함하므로 실제 모든 손실 건수를 증명하지 않는다. 기기의 실행시간·메모리·제어 지연 예산은 미검증이다.

WebCarrot는 다음을 분리해 표시한다.

- **관측 저장값:** 설정 파일을 스캔한 저장 단위 값이다. 소비 지점의 수치로 덮어쓰지 않는다.
- **읽힌 값·내부 사용값:** `read_value`·`effective_value`는 소비 코드의 물리 단위다. `loaded / used / latched / bypassed / unsupported / unknown`과 `stored / learned / platform_fixed / inherited / derived / retained_previous / unknown` 출처를 함께 본다. 생성자 고정이나 차량 고정값은 저장값 변경의 효과로 오인하지 않는다.
- **두 가지 신선도:** `fresh`는 이벤트·프로세스 식별·순번의 현재성이고 `read_freshness`는 항목별 실제 읽기 시각이다. 최근 이벤트의 생성자 고정 내부값을 관측했더라도 설정 읽기는 오래됐을 수 있다. 오래된 이벤트의 숫자는 현재값으로 표시하지 않으며 공백·재시작도 별도 표시한다.

관측에는 boot/process/object 식별자, 순번, 생성·읽기 시각, cycle/phase와 제한된 코드 근거가 있다. 화면은 원시 식별자·개인 경로를 노출하지 않는다. 이 표본으로 `consumer_application_ack`, `device_qualified`, 추천·적용 자격을 true로 만들지 않는다. 여러 소비 지점의 전환·최대 지연·모든 쓰기 경로를 확인한 전체 반영 계약은 후속 단계다.

기록 기능을 함께 켜면 consumer 이벤트는 설정 관측과 **동일한 `settings.jsonl` journal의 8MiB/10,000기록 예산**을 공유한다. 그중 consumer 기록은 최대 절반인 **4MiB/5,000기록**으로 제한하고, 전체 journal 상한 앞에 64KiB와 기록 1개를 남기도록 먼저 거절한다. consumer 예산 거절은 관측 이력을 `partial`과 미기록 건수로 표시하며, 기존 설정 관측·진단·원본 보존 이력 작성자는 남은 전체 예산 안에서 계속 동작한다. 실시간 소비값 수신도 계속하며 기록을 자동 확장·회전·삭제하지 않는다. 따라서 한 시간 세션의 소비 관측 전체가 영구 보존된다고 약속하지 않는다.

정상 종료 기록에는 `consumer_budget_rejections`를 내구 기록하고, 읽기 전용 `inspect-session`은 이를 `consumer_observations.budget_rejected_events`로 보여 준다. 이전 형식이나 종료 기록 없는 세션은 `null`(Python `None`)로 남기며 거절 0건으로 추정하지 않는다. 같은 journal의 순서·해시 연결은 보존하지만 consumer 이벤트를 가까운 저장 설정 revision에 자동 결합하지 않는다. journal 작성 시각, 소비 지점의 읽기·사용 시각, 실제 적용 완료는 서로 다른 근거다. `inspect-session`의 파일 무결성 검사를 통과해도 전체 적용 완료나 원본 범위 완전성으로 승격하지 않는다.

## PC 오프라인 후보 계산

현재 후보 엔진은 단위와 출처를 명시한 정규화 자료에서 수식을 계산하고, 현재값과 수치 후보를 비교하는 **host 실험**이다. 실제 주행 rlog를 이 명령이 자동으로 독립 반응 자료로 변환하지는 않는다. 설정 관측·원본 보존을 완료했다는 사실만으로 수치 추천의 입력 조건이 충족되지는 않는다. 기존 제어기·Params·실시간 수신 경로에 숫자를 쓰지 않는다.

저장소 루트에서 다음 합성 예제를 실행한다. `candidate_evidence_synthetic.json`과 `candidate_profile_synthetic.json`에는 실제 차량과 무관한 기준값·세 개의 분리된 자료 구간·host 한도가 들어 있다. 아래 예제의 정책·기하 계산은 표준 Python으로 실행할 수 있고, 토크 TLS 경로는 NumPy도 필요하다.

```powershell
$candidateOutput = Join-Path $env:TEMP ("fms-candidate-" + [guid]::NewGuid().ToString("N"))
python -m openpilot.selfdrive.carrot.control_advisor evaluate-candidates `
  openpilot/selfdrive/carrot/control_advisor/examples/candidate_evidence_synthetic.json `
  --profile openpilot/selfdrive/carrot/control_advisor/examples/candidate_profile_synthetic.json `
  --output $candidateOutput

python -m openpilot.selfdrive.carrot.control_advisor verify $candidateOutput
```

기대 결과는 `state: complete`, `scope: offline_candidate_calculation`이다. 새 폴더의 `result.json` 안 `report.items`에 합성 기준에 맞춘 `CruiseMaxVals0` 후보 `130`(물리 단위 `1.3m/s²`)과 `PathOffset` 후보 `20`(`0.2m`)이 `search_candidate`로 나온다. **이 숫자는 예제의 정답이며 사용자 차량에 권하는 설정값이 아니다.** 입력·프로파일 사본과 해시, 계산 코드 근거, 결과를 묶고 동일 출력 경로에는 덮어쓰지 않는다. `complete`는 결과 묶음 작성 완료이며 전체 항목의 계산 성공·튜닝 완료를 뜻하지 않는다.

입력 자료의 주요 계약은 다음과 같다.

- `settings_raw`는 저장 단위이고 각 신호는 `value / unit / source / source_mono_ns`를 가진다. 코드가 다른 단위나 시간축을 추정해 고치지 않는다. 설정 epoch·차량/소비 경로·선언한 코드와 원본 SHA를 함께 제공한다.
- `fit`는 원시 계수 추정, `select`는 후보 선택, `holdout`은 선택된 한 후보의 최종 확인에만 사용한다. 구간·의존 시간·원본 범위를 공유하는 자료는 분리된 확인 근거로 취급하지 않는다. holdout을 보고 다음 후보를 다시 선택하지 않는다.
- 개입·회복·무효·잠정 구간과 미래·오래된 신호를 사용하지 않는다. 독립 측정이 필요한 반응에 현재 제어기의 차량 모델로 계산한 값을 대신 넣으면 보류한다. 후보마다 사용 자료를 유리하게 바꾸거나 큰 오차 자체를 제외하지 않는다.
- 프로파일은 출처 종류, 자료량, 탐색 개수·변경 폭·저장 단위 간격, 개선 기준과 독립 출력 guard를 명시한다. 메뉴 범위·간격은 탐색의 직렬화 조건이며 검증된 단회·누적 변경 한도가 아니다. 필요한 guard가 없으면 검색 결과를 확정하지 않는다.

입력의 원본·코드 해시와 측정 출처는 이 단계에서 **자료 제공자의 선언**이다. 결과 묶음 검사는 입력/프로파일 사본·계산 결과의 해시 연결을 확인한다. 선언한 실제 rlog/CAN·원본 파일과 각 표본의 유래를 자동 대조한 상태는 아니므로 `raw_sources_verified=false`를 유지한다. `static_formula`의 오차 개선은 정책 수식이나 독립 보정 모델의 적합도이며 실제 폐루프 주행 성능 개선으로 확대하지 않는다.

상태와 추정 종류도 구분한다.

| 표시 | 뜻 |
|---|---|
| `raw_estimate` | 원시 계산값은 있으나 후보 선택·확인을 통과하지 못한 상태 |
| `search_candidate` | 명시된 host 실험의 선택·holdout·guard를 통과한 미검증 후보 |
| `keep_current` | 개선 또는 확인 조건을 충족하지 못해 이번 실험에서 현재값 유지 |
| `blocked` | 필요한 입력·출처·프로파일·소비 경로·백엔드 근거 부족 |
| `unsupported` | 해당 계산 경로가 현재 구현돼 있지 않거나 구조적으로 비활성 |

`estimate_kind=parameter_calibration`과 `diagnostic_proxy`는 다르다. 토크 TLS의 마찰 잔차 spread는 물리 마찰의 식별값이 아니고, 신호 사이의 총 경로 지연은 곧바로 `SteerActuatorDelay`에 넣을 값이 아니다. 두 진단 proxy는 원시값과 사유를 보이며 숫자 후보로 승격하지 않는다. PID 계수·평활화의 주행 효과에는 식별된 폐루프 차량 응답 모델이 필요하다. MPC 가중치·시간 변환에는 해당 궤적/solver 평가 경로가 필요하며, 준비되지 않은 경우 명시적으로 보류한다. 이미 수신한 차량 반응을 고정한 채 새 제어값만 계산해 주행 개선이라고 판정하지 않는다. `CustomSR` 수치 계산도 이미 선택된 수동 모드 안에서만 다루며 자동/수동 특수값을 전환하지 않는다.

이 단계의 `recommendations_enabled`, `apply_enabled`, `device_qualified`는 항상 false다. 결과를 보고 사용자가 적용 버튼으로 차량 설정을 바꾸는 기능·차량 검증 프로파일·부하 검증은 아직 연결하지 않았다.

WebCarrot에 결과를 표시하려면 웹서버 시작 환경에 `CARROT_CONTROL_ADVISOR_CANDIDATE_REPORT_DIR`을 **완료된 결과 묶음 또는 아래 로그 분석 collection의 절대 디렉터리**로 지정한다. 서버는 시작할 때 한 번 읽기 전용으로 검사해 보관한다. 새 결과를 표시하려면 새 완료 결과를 선택하고 서버를 다시 시작해야 한다. 웹 요청으로 경로를 지정하거나 파일을 업로드해 계산시키는 API는 없다. 일반 수신 세션과 오프라인 후보의 근거·현재성은 별도로 표시하며 적용 버튼은 계속 비활성이다. 이 설명은 기기를 지금 재시작하거나 배포하라는 지시가 아니다.

## 실제 rlog에서 후보 입력 자동 추출

한 collection에는 같은 주행 route의 로그만 넣는다. 서로 다른 route나 부팅의 monotonic 시간을 비교해 최신 결과를 정하지 않는다. 여러 파일의 동일 route를 파일 이름이나 검증된 세션 영수증에서 연결할 수 없으면 함께 계산하지 않고 명시적으로 거절한다.

`evaluate-logs`는 직접 선택한 rlog와 선택적인 종료된 기록 세션을 읽어 후보 입력을 자동으로 만든다. 위 `evaluate-candidates`처럼 EvidencePack JSON을 손으로 작성할 필요는 없다. native decoder 의존성이 있는 Python을 사용하고, 새 출력 디렉터리를 지정한다.

```powershell
$logCollection = Join-Path $env:TEMP ("fms-log-candidates-" + [guid]::NewGuid().ToString("N"))
python -m openpilot.selfdrive.carrot.control_advisor evaluate-logs `
  C:/logs/rlog1.zst C:/logs/rlog2.zst `
  --profile openpilot/selfdrive/carrot/control_advisor/examples/log_candidate_profile.json `
  --objectives openpilot/selfdrive/carrot/control_advisor/examples/log_candidate_objectives.json `
  --output $logCollection

python -m openpilot.selfdrive.carrot.control_advisor verify-log-sources `
  $logCollection C:/logs/rlog1.zst C:/logs/rlog2.zst
```

파일이 하나면 두 번째 rlog 인수를 뺀다. 종료된 관측·보존 세션이 있으면 첫 명령에 `--session C:/advisor/evidence/SESSION_ID`를 추가한다. 예제 프로파일은 `recorded` 입력을 허용하는 **미검증 host 프로파일**이며 차량용 검증 프로파일이 아니다. objectives 파일은 항목별 독립 기준·단위·출처를 명시하는 입력이다. 예제의 값이나 허용 폭을 사용자 차량의 권장 설정으로 사용하지 않는다. 필요한 독립 기준이 없으면 임의로 채우지 않고 해당 항목을 보류한다.

확인 가능한 안정 설정 구간(epoch)마다 개입·회복·신호 공백 등의 제외 마스크와 `fit / select / holdout` 입력을 자동으로 분리한다. 자동 분할은 독립 반응·소비 경로·모든 의존 신호의 확보를 대신하지 않는다. 세션 journal이 없을 때 rlog의 `initData` 설정은 과거 참고 정보일 뿐 현재 저장값의 증거로 승격하지 않는다. 저장값·전환 경계를 확인할 수 없는 항목은 미확인·보류로 남는다.

각 구간의 결과는 collection 아래 별도 불변 묶음으로 저장한다. 화면은 시간상 **가장 최근 구간**을 선택하며, 그 구간이 `blocked`여도 과거의 성공 후보로 대체하지 않는다. 현재 한도는 묶음당 8MiB, collection 전체 32MiB, 최대 32개 epoch다. 초과를 숨기거나 기존 결과를 덮어쓰지 않으며 이 한도는 PC 예비 예산으로 콤마 자원 검증값이 아니다.

원본 연결 상태는 다음처럼 구분한다.

- **생성 당시 확인:** 선택한 원본 파일의 해시와 native schema 해석·추출 결과 연결을 확인한다. 신호별 의미 매핑은 현재 일부만 연결돼 있으며 모든 필드의 의미·독립성·주행 효용을 검증했다는 뜻이 아니다. `raw_sources_verified`, 차량 검증·추천·적용 플래그는 계속 false다.
- **보존과 현재 접근:** 종료 세션의 검증된 archive 영수증으로 연결된 경우와 세션 미연결·원본 참조만 있는 경우를 나눈다. 원본을 참조했다고 collection에 원본 전체를 복사·보존한 것으로 표시하지 않는다. 화면의 현재 원본 접근 상태는 `not_checked`다.
- **명시적 재확인:** `verify-log-sources`는 사용자가 명령 인수로 다시 선택한 원본과 생성 당시 근거를 읽기 전용으로 대조한다. 영수증 안의 과거 경로를 자동으로 따라가지 않으며 서버의 시작 시점 snapshot이나 차량 검증 상태를 바꾸지 않는다.

정책 수식의 정적 계산 경로는 현재 26개 항목에 있지만, 실제 rlog 추출기가 확보한 입력과는 범위가 다르다. `vEgo`·`myDrivingMode`와 명시적인 Cruise 관련 곡선 입력 등 연결된 자료만 사용한다. 그 밖의 의존 신호·설정 이력·독립 기준이 빠진 경로는 보류한다. MPC 궤적·solver 평가와 차량 폐루프 검증이 구현된 것으로 해석하지 않는다.

웹서버를 다음에 시작할 환경에 `$env:CARROT_CONTROL_ADVISOR_CANDIDATE_REPORT_DIR = $logCollection`을 지정하면 이 collection을 읽는다. 기존 단일 후보 묶음도 계속 지원한다. 화면에는 원본 경로·해시·소스 식별자를 표시하지 않으며 원본 업로드·외부 AI 호출·설정 적용·CPU 코어 지정은 수행하지 않는다.

## 완료 로그의 주기 분석과 자동 결과 갱신

정적 보고서와 달리 아래 기능은 수집 중에 완료된 로그를 분석한다. 앞의 실제 수신·설정 이력·원본 보존 설정에 더하여 다음 환경을 **웹서버를 시작할 때** 지정한다. 이는 설치 방법을 설명하는 계약이며 이 작업에서 실제 기기 환경을 변경하지 않았다.

| 환경변수 | 값과 의미 |
|---|---|
| `CARROT_CONTROL_ADVISOR_AUTO_ENABLED` | `1`일 때만 분석 담당과 child를 허용한다. 미설정은 꺼짐이다. |
| `CARROT_CONTROL_ADVISOR_AUTO_PROFILE` | host 후보 프로파일의 절대 파일 경로. 예시 파일의 조건은 실차 검증 한도가 아니다. |
| `CARROT_CONTROL_ADVISOR_AUTO_OBJECTIVES` | 명시적인 목표 정책 파일의 절대 경로. 측정 가속도를 목표로 자동 복제하지 않는다. |
| `CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT` | 별도 분석 결과 루트의 절대 경로. 분석기와 웹의 결과 조회가 같은 경로를 사용한다. 원본·설정 경로와 분리한다. |

`CARROT_CONTROL_ADVISOR_CANDIDATE_REPORT_DIR`과 자동 결과 루트를 함께 지정하면 웹은 설정 충돌을 표시한다. 기존 정적 보고서 방식에서 자동 방식으로 바꿀 때는 정적 변수부터 해제한다. 환경값을 바꾸기 위해 웹에서 파일 경로나 명령을 제출하는 기능은 없다. 합성 데모는 실제 수신의 자동 분석 설정을 상속하지 않으며, 자동 분석 검증은 native 형식의 별도 PC fixture를 사용한다.

1. 로그 화면에서 **제어 분석**을 열고 기존 세션 키를 입력한다. 갱신 간격을 기본 **5분** 또는 **3분**으로 선택한 뒤 실제 수신을 시작한다. native 의존성이 없는 PC에서 실제 수신을 시작하면 기존처럼 사용 불가가 표시된다.
2. 원본 복사와 archive 영수증의 journal 저장이 완료될 때까지 대기한다. 열린 로그·복사 중 파일·journal에 아직 연결되지 않은 사본은 계산하지 않는다. 이력 작성 담당은 작은 checkpoint만 넘기고, 별도 분석 프로세스가 확정 prefix 사본과 원본 참조를 검증한다.
3. 주기마다 선택된 완료 로그를 세그먼트별 분석 구간으로 계산한다. **주기는 실행 간격이고, 실제 확보된 입력 범위는 화면의 대상 구간과 파일 수로 확인한다.** 각 구간의 후보는 전체 3/5분의 통합 최적값이 아니며 설정 epoch와 분석 window를 구분한다. 일부 구간의 예산 초과·근거 부족은 별도 상태로 남는다.
4. 웹서버는 약 5초 간격으로 게시 상태를 확인하고 화면은 기존 상태 조회를 통해 갱신한다. 새 결과마다 서버를 재시작할 필요가 없다. 작업 상태·게시 버전·완료/보류/실패 구간 수를 확인한다. 처리 중·실패·부분 완료·오래된 결과를 이전 성공 후보로 덮어 표시하지 않는다.
5. **수집 종료**로 신규 예약을 막고 분석 프로세스를 종료한다. 원본·완료 결과·미완료 산출물은 보존한다. 기기 CPU·열·I/O 한도와 실제 차량 적용은 아직 검증하지 않았다. 아래 요청 등록과 PC 모의 적용은 별도의 기능이다.

분석 결과 루트에는 단일 작성자 잠금과 별도 저장 예산을 적용한다. 동일 입력의 완료 결과 재사용, 중단 결과의 미완료 판정, 원자적 최신 결과 게시를 구분한다. 공간 부족·시간 초과·변조·동시 실행 시 원본을 삭제하거나 한도를 자동으로 늘리지 않는다. HTTP 상태 조회 안에서 압축 해제나 후보 계산을 수행하지 않는다.

결과 루트는 설정·원본·보존 루트와 같거나 그 상위/하위 디렉터리여서는 안 된다. 경로가 겹치면 자동 분석만 실패 상태로 두고 기존 기록은 계속한다. 초기 host 한도는 분석 cache 512MiB/32작업, 작업당 120초·8개 window·출력 128MiB이며 실기기에서 검증한 한도가 아니다. 실패·취소 산출물도 예산에 포함하고 자동 삭제하지 않는다.

새 원본 없이 일반 poll만 늘어나면 같은 로그를 재분석하지 않는다. 단, 실제 결과에 causal consumer/right bracket의 일시적 누락이 보고됐을 때만 새 checkpoint로 다음 정규 주기에 한 번 재시도한다. 일반적인 근거 부족·미지원 항목·오래되거나 모순된 관측에는 이 예외를 적용하지 않는다. 재시도 이후에 늦게 도착한 근거는 다음 새 원본이나 명시적 재분석 전까지 반영되지 않는다.

제거할 때 이 환경변수를 해제하고 기존 전용 기능 폴더·웹/서버 접점·생성물·FMS 보존 자산을 함께 갱신한다. 후보 계산을 위해 추가한 제어 훅이나 writer는 없다. 외부의 결과 묶음과 원본 증거는 코드 제거만으로 지우지 않는다.


## 적용 요청과 PC 모의 전후 비교

요청 등록, 설정 저장, 소비 지점의 반영 관측, 비교 결과는 각각 다른 상태다. `apply_enabled`, `recommendations_enabled`, `device_qualified`는 계속 false이며, 요청을 접수했다고 차량 적용이 가능해진 것은 아니다.

- 서버의 `CARROT_CONTROL_ADVISOR_REQUEST_ROOT`를 별도의 절대 경로로 설정하고 기존 세션 키를 사용하면 요청 기록 기능을 준비할 수 있다. 이 루트는 설정·원본·보존·자동 분석 루트와 같거나 상위/하위로 겹쳐서는 안 된다. 잘못된 요청 설정은 요청 기능만 차단하며 기존 수집·분석을 막지 않는다.
- **실시간 추천** 카드에는 현재 후보의 요청 가능 여부와 부족한 근거가 나온다. 요청은 현재 분석 결과·항목·관측 설정과 결합하며 임의의 새 숫자나 파일 경로를 받지 않는다. 요청 직전 후보나 설정이 달라졌으면 이전 숫자로 진행하지 않는다.
- **차량 대상**의 `적용 요청 등록`은 내구성 있는 `보류` 기록을 남긴다. 사유는 차량 writer 미연결이다. 이후 기능을 켜더라도 이미 보류된 요청을 자동 실행하지 않는다. 실제 Params 쓰기는 없다.
- **명시적인 PC 시험**의 `PC 모의 적용 요청`은 별도 저장소의 현재값·설정 revision·의존 조건을 비교한 후 조건부로 저장한다. PC 모드는 서버 구성에서만 주입하며 웹 요청이나 환경변수로 차량 저장소를 PC 대상으로 바꿀 수 없다. 별도 시험 소비자가 저장소를 읽어 반영을 관측한다.
- **적용 전후 비교**에서 요청 상태와 전후 관측 결과를 확인한다. 필요한 소비 지점의 관측이 없으면 저장 완료를 반영 완료로 올리지 않는다. 비교 조건이 부족하거나 다른 관련 설정이 함께 바뀌면 사유를 표시한다. 주행 개선이나 인과효과를 확정하는 화면이 아니다.

같은 요청 ID의 재전송은 같은 결과를 돌려주며, 내용을 바꿔 재사용하면 충돌이다. 저장 의도를 기록하기 전에는 취소할 수 있다. 이미 저장 의도가 기록된 요청은 취소 성공으로 위장하거나 자동 원복하지 않고 실제 저장 영수증을 확인한다. 재시작 후 미확정 요청을 자동 재실행하지 않는다. 저장값이 우연히 같다는 이유만으로 성공 처리하지 않는다.

비교의 before 자료와 분석 조건은 요청 시 고정한다. after는 필요한 소비 지점 중 가장 늦은 반영 관측과 전환 여유 이후부터 선택한다. 기존 운전자 개입·개입 직전·회복·신호 공백·미완료 꼬리 제외 규칙을 적용한다. 대상·세션·route·부팅·코드·제어 경로·모드·단위·목적이 맞는지 확인하고, 명시한 조건 대역에서 공통으로 확보된 자료를 비교한다. 원시 오차와 조건을 맞춘 bias/RMSE/p95, 유효 시간·표본 수·제외 사유를 구분한다. 후보 정책의 적합도와 실제 추종 오차, 모델 유래 횡응답과 독립 측정을 혼동하지 않는다.

PC 시험 결과에는 모의 자료라는 표시를 유지한다. 실제 차량의 주행 성능·전체 반영 ACK·CPU 부하를 검증한 결과가 아니다. 요청 기능을 제거할 때는 요청 환경변수를 해제하고 전용 모듈·서버/웹 접점·FMS 보존 자산을 함께 갱신한다. 외부 요청 기록과 원본 증거는 코드 제거만으로 삭제하지 않는다.


요청 API는 기존 `/api/control_advisor` 아래 `POST /apply-requests`, `POST /apply-requests/{id}/cancel`, `GET /apply-requests/{id}`다. 변경 요청은 기존 토큰·같은 Origin·중복 JSON 키 거부·4KiB 본문 제한을 재사용한다. 본문은 `request_id`, `target_id`, `session_id`, `artifact_sha256`, `result_revision`, `item_key`, `expected_current_raw`, `expected_settings_revision`, `dependency_digest`만 받는다. 후보의 실제 새 값은 서버가 검증한 분석 묶음에서 가져온다. 공개 조회는 요약만 제공하고 파일 경로·원본 journal·전체 의존값을 노출하지 않는다.

요청 기록의 기본 PC 예산은 최대 128개 요청·2,048개 사건·4MiB다. 공간이나 기록 한도가 부족하면 신규 기록을 거부하고 기존 기록을 자동 삭제하지 않는다. 기록 동기화 결과가 불확실하면 해당 작성자를 재사용하지 않으며, 보존된 기록 검증 없이 성공으로 처리하지 않는다. 이 예산은 콤마에서 측정한 자원 한도가 아니다.

## 주차 요약과 Codex 2차 검토 파일

WebCarrot의 **로그 → 제어 분석 → 주차 요약**에서 누적 분석과 설정 적용 이력을 확인한다. 주차 관측, 기기의 offroad 관측, 분석 확정 범위와 마지막 원본 로그의 보존 상태는 별도로 표시한다. P 기어·정지·제어 비활성이 유효한 신호로 일정 시간 관측되어야 주차 상태가 된다. 신호 대기 중 속도 0이나 수집 종료만으로 주차가 되지는 않는다. 이 판정은 미검증 host profile이며 실제 차량에서의 신호 신뢰도는 별도 확인해야 한다.

요약이 표시되어도 마지막 개입 판정 여유 구간과 열린 로그는 대기 중일 수 있다. 원본이 없거나 설정 반영을 관측하지 못한 항목은 누락·미확인으로 남긴다. 분석 대상 구간의 지표와 설정별 관측 노출을 전체 주행 시간이나 실제 성능 개선으로 해석하지 않는다. 수집이 끝난 요약과 이전 결과는 역사 자료이며 현재 적용 가능한 추천을 의미하지 않는다.

로컬 내보내기를 준비하려면 서버의 `CARROT_CONTROL_ADVISOR_EXPORT_ROOT`를 기존 설정·원본·보존·자동 분석·요청 기록과 겹치지 않는 별도 절대 경로로 지정한다. 기본은 OFF이며 잘못된 내보내기 설정은 기존 FMS·수집·분석을 변경하지 않는다. 기록과 분석에는 기존 `CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT`, `CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT` 구성을 사용한다. 웹에서 경로를 입력하지 않는다.

1. 주차 요약에서 요약 revision과 자료 부족·미완료 범위를 확인한다.
2. 기존 세션 키를 입력하고 내보내기를 요청한다. 원본 로그 포함은 기본 해제이며 필요한 경우 직접 선택한다.
3. 작업이 완료되면 ZIP을 내려받는다. 진행 중 취소는 원본이나 이전 완료 ZIP을 삭제하지 않는다. 중단된 작업은 완료로 바꾸거나 자동 재실행하지 않는다.
4. 검토를 요청할 때 ZIP을 직접 선택해 Codex에 전달한다. 프로그램이 외부에 업로드하거나 Codex를 자동 호출하지 않는다.

원본 rlog에는 위치·식별정보가 포함될 수 있다. 원본을 빼더라도 설정·소비 관측·적용 이력에는 경로와 주행 맥락이 남을 수 있으므로 **익명화된 파일이 아니다**. ZIP의 `REVIEW_README.md`와 `review.json`은 포함 파일·해시·민감도·누락 범위와 2차 검토 결과에 연결할 요약/manifest 해시를 안내한다. 파일 무결성 검증은 자료의 완전성, 추천의 적합성 또는 차량 적용 승인이 아니다.

다운로드한 ZIP은 원래 로그 저장 위치에 접속하지 않고 확인할 수 있다.

```powershell
python -m openpilot.selfdrive.carrot.control_advisor verify-review C:/review/review.zip
```

성공한 검증은 JSON과 종료 코드 0을 반환한다. 파일 변조·잘못된 경로·한도 초과는 오류와 종료 코드 2를 반환한다. 검증 자체는 원본 복구·설정 쓰기·외부 전송을 수행하지 않는다. 코드/스키마 버전 차이로 재계산할 수 없는 자료도 저장된 바이트의 무결성과 재해석 가능성을 구분한다.

기능을 제거할 때 내보내기 환경변수와 전용 모듈·서버/웹 접점·FMS 보존 자산을 함께 해제한다. 외부 원본·요청 ledger·완료 ZIP은 코드 제거만으로 삭제하지 않는다. 실기기 부하·저장 공간·CPU 코어 지정·전원 차단 내구성은 아직 검증하지 않았다.

## Codex 검토 결과 기록

내려받은 ZIP을 검토할 때 다음 명령으로 해당 묶음의 해시가 채워진 **미검토 초안**을 만든다. 출력 파일이 이미 있으면 덮어쓰지 않는다.

```powershell
python -m openpilot.selfdrive.carrot.control_advisor review-template C:/review/review.zip --output C:/review/review-draft.json
```

1. ZIP과 초안을 Codex에 전달하고 확인한 범위·미확인 범위, 근거·판정·불확실성·후속 조건을 작성하도록 요청한다. 초안을 만든 것만으로 검토가 수행되지 않는다. `draft`는 등록할 수 없으며 실제 검토 뒤 `final` 문서로 완성해야 한다.
2. **로그 → 제어 분석 → 주차 요약**의 해당 내보내기 카드에서 반환된 검토 JSON을 선택하고 등록한다. 기존 세션 키가 필요하다. JSON만 로컬 웹서버에 전달하며 외부 AI 호출이나 자동 업로드는 없다.
3. 서버가 묶음·요약·분석 결과 해시와 요약 버전을 대조한다. 다른 ZIP의 결과, 초안, 변조·충돌·잘못된 형식은 등록하지 않는다. 동일 요청의 재시도는 새 리뷰를 중복 생성하지 않는다.
4. 같은 카드의 검토 이력에서 대상 요약 revision, 검토 revision과 판정을 확인한다. 후속 검토는 바로 이전 검토 해시와 다음 revision을 사용한다. 이전 기록은 덮어쓰지 않으며 새 요약에 자동으로 복사하지 않는다.

검토자·모델 이름은 제출 문서의 선언이며 신원이 인증된 것이 아니다. 코드·DBC·분석기 버전도 내보낸 근거와 대조 가능한 정보, 선언만 있는 정보, unknown을 구분한다. `summary_only`는 요약 검토이며 분석 결과를 검토한 추천으로 표시하지 않는다. 검토 의견에 수정 숫자가 있어도 새로운 기계 후보·설정 적용 승인·실차 개선으로 승격하지 않는다.

리뷰 기록은 완료 ZIP을 변경하지 않고 해당 내보내기 작업의 `reviews` 디렉터리에 보관한다. 원래 묶음이 없거나 검증되지 않으면 신규 등록을 막고 기존 검토를 과거 기록·현재 연결 미확인으로 표시한다. 기능 코드 제거 시 외부의 ZIP·리뷰·원본·설정 자료는 그대로 보존한다.

리뷰 JSON과 등록 요청은 128KiB, 검토·미검토 범위는 각각 128개, 소견은 64개까지 받는다. 한 내보내기 작업에 최대 32개 검토 revision을 보관하고 저장 예산은 4MiB다. 한도를 넘으면 기존 자료를 삭제하거나 조용히 잘라내지 않고 등록을 거부한다. 원본 rlog는 리뷰 JSON의 파일 선택 대상으로 받지 않는다.

`POST /api/control_advisor/export-jobs/{job_id}/reviews`는 `request_id`와 `review`를 받고, `POST /api/control_advisor/export-jobs/{job_id}/reviews/query`는 빈 객체로 상세 이력을 조회한다. 두 경로 모두 기존 토큰과 동일 Origin이 필요하다. 공개 상태 응답에는 상세 의견과 검토자 정보를 넣지 않는다. `reviewed_recommendation` 판정에는 실제 검토한 분석 결과 범위와 근거가 필요하며, 선택한 각 분석 결과의 검토·미검토 여부를 명시한다. 작성자의 진술이 사실이라는 인증이나 차량 적용 승인으로 해석하지 않는다.


## 기기에서 시작·적용·원복

1. 콤마의 WebCarrot QR로 접속한 뒤 **로그 → 제어 분석**을 연다. 기기용 QR 인증이 성공하면 세션 키를 별도로 입력하지 않는다. 일반 URL·PC 경로에서는 기존 수동 인증을 사용한다.
2. 차량 기준이 미확인이라면 동일 차량의 기존 기준을 선택하거나 새 차량임을 명시한다. 현재 설정과 최초 백업은 별개로 보존된다. 수집 시작 후 탭을 닫아도 세션은 유지된다.
3. 근거가 연결된 후보 카드에서 적용을 요청한다. 서버가 현재 값·차량·신선한 상태·개입 회복·소비 지점 조건을 다시 확인한다. ‘저장됨’은 제어기가 새 값을 사용했다는 뜻이 아니며 후속 관측과 비교를 기다린다.
4. **차량 설정 기준 백업 다운로드**로 최초 원시 바이트를 별도 보관한다. 적용 기록의 원복 버튼은 해당 요청의 이전 값으로 복구한다. 분석 종료 후의 명시적 비주행 원복은 실제 offroad 상태를 확인하고 다음 시동 반영 대기로 표시한다. 브랜치를 바꿔도 Params에 저장된 값은 자동으로 되돌아가지 않는다.

기기 경로 자동 구성과 숫자 추천의 근거는 별개다. 분석 profile 또는 목적 곡선이 없으면 수집·진단은 가능하지만 필요한 자료를 가정해 추천 숫자를 만들지 않는다. 항목별 계산 미구현·부족 신호·현재 적용 보류 이유를 화면에서 구분한다. 현재 PC/WSL 검증은 실제 차량의 제어 성능이나 콤마 CPU 여유를 입증하지 않는다.

실시간 수집은 설정 이력을 파일 단위로 분할해 기록한다. 회전 뒤에도 순번과 해시를 이어가며 분석·내보내기는 필요한 시간 창과 참조 기록을 검증한다. 선택 구간만 검증된 경우 전체 기록 검증과 구분하고, 원본과 이전 스냅샷은 보존한다. 소비 관측 5,000건을 이유로 수집을 멈추지 않으며 기존 v1 기록도 읽을 수 있다. 디스크 예산 초과·쓰기 실패는 명시적인 상태로 알린다.
