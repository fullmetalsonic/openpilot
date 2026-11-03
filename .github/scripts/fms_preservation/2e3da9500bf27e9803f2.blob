# Carrot PC 로그 수집기

이 WIP의 **fullmetalsonic에게 로그 보내기** 버튼으로 로그를 fullmetalsonic의
개인 PC 한 곳에 보냅니다. 폰의 웹캐럿 화면이 로그를 중계하며 폰은 Tailscale에
연결되어 있어야 합니다. 콤마 IP 입력·등록코드·별도 계정이나 콤마의 VPN 가입은
필요하지 않습니다. 고정 비공개 수신 주소는
`http://minipc-amd5500u.tail8d9376.ts.net`입니다. 장치가 최초 전송 확인 때 만든
무작위 장기키의 SHA256만 PC에 등록하며 같은 키의 재시도는 같은 기기 ID를
돌려줍니다. 키 원문은 브라우저 영구 저장소·URL·로그·PC 상태 파일에 저장하지
않으며 폰의 중계 동작 중 메모리에서만 사용합니다.

## 실행

제공된 바탕화면 바로가기 또는 `start.cmd`를 실행하세요. 창에는 수신 상태,
고정 주소, 기기 수와 저장량/한도가 표시됩니다. **수신 일시중지/재개**와
**로그 폴더 열기**를 제공합니다. 창을 닫으면 수신기도 종료합니다. Tkinter가
없으면 콘솔에서 `p`(일시중지/재개), `s`(상태), `q`(종료)를 사용합니다.
Python 3.10 이상과 표준 라이브러리만 사용합니다.

```powershell
python tools/carrot_pc_logs/receiver.py --output "D:/Codex/오픈파일럿/로그기록"
```

기본 실행은 `127.0.0.1:8766`의 로컬 수신기만 엽니다. **로컬 프로그램 실행**과
**폰에서 접속할 비공개 연결 완료**는 다르며 상태에 이를 구분합니다. 설치
실행기의 `--tailscale` 옵션으로 기존 Tailscale Serve를 foreground로 관리합니다.

```powershell
python tools/carrot_pc_logs/receiver.py --output "D:/Codex/오픈파일럿/로그기록" --tailscale
```

`--tailscale`을 명시한 경우에만 이미 설치된 Tailscale의 PC 이름과 기존 Serve
설정을 확인하고, 로컬 포트 bind 성공 후 `tailscale serve --yes --http=80 http://127.0.0.1:8766`을 foreground
자식으로 실행합니다. 기존 설정이나 호스트 이름 충돌이면 변경하지 않고 수신
실패를 표시합니다. 종료 때 자신이 실행한 자식만 종료하며 `--bg`, 설정 reset,
방화벽 변경, 설치, 부팅 자동실행은 수행하지 않습니다. 실제 Serve 접근망과
폰의 Tailscale 접속 확인은 별도이며 자식 프로세스 실행만으로 준비 완료를
단정하지 않습니다. Serve status에서 해당 호스트의 80번 HTTP와 본인 8766 proxy를
확인하기 전에는 **PC 비공개 HTTP 연결 준비 중**으로 표시합니다.
구성 확인 후에도 폰 연결은 미확인으로 구분합니다. 인터넷 공개 연결은 제공하지
않습니다. 기존 Tailscale 계정의 암호화된 망 안에서 HTTP를 사용하며 별도
HTTPS 최초 동의나 공개 인증서를 전제로 하지 않습니다. production endpoint는
위의 고정 origin 하나만 허용하고 임의 HTTP 주소는 허용하지 않습니다.

상태 폴더 기본값은 `%LOCALAPPDATA%/CarrotPcLogs/receiver`이고 `--state`로 별도
지정할 수 있습니다. 기본 로그 폴더는 현재 폴더 아래 `로그기록`입니다. 상태와
로그 폴더는 서로 겹치지 않아야 합니다. 기존 현장 로그는 이동·삭제하지 않습니다.

## 저장과 검증

```text
<output>/YYYY-MM-DD/<deviceId>/<route>/<route--segment>/rlog.bz2
<output>/날짜미확인/<deviceId>/<route>/<route--segment>/rlog.zst
```

날짜는 `initData.wallTimeNanos`의 주행 시작 시각을 KST로 다시 계산합니다.
PC 수신일이나 파일 수정일로 대체하지 않습니다. 자정 통과·0번 세그먼트 누락·
지연 전송에도 원래 시작일을 유지합니다. 날짜가 불명확하면 `날짜미확인` 폴더에
저장하고 원인과 수신 시각을 영수증에 별도로 남깁니다. 여러 기기가 같은 작업·
파일·주행 이름을 보내도 기기별로 분리합니다.

1MiB 이하 청크를 fsync하고 `.partial` 크기로 재개합니다. 전체 크기·SHA256
검증, 덮어쓰지 않는 원자 게시와 durable `.receipt.json` 저장이 끝나야 완료를
알립니다. 연결이나 응답 유실 시 GET/동일 작업 재등록으로 증거를 복구합니다.
기존 파일 내용이나 영수증이 다르면 보존하고 ACK하지 않습니다. 잘못된 전체
해시의 partial은 `.invalid-<ID>`로 보존합니다. 정상 로그 자동삭제는 없습니다.
Windows는 덮어쓰기 없는 rename, Linux는 `renameat2(RENAME_NOREPLACE)`를
사용하며 다른 OS에서 안전한 원자 게시가 없으면 완료 처리하지 않습니다.

## 비공개 수신 한도

정책은 `Policy` 한 곳에 있습니다. 기본값은 다음과 같습니다.

| 항목 | 기본값 |
| --- | --- |
| 누적 저장 / 남길 디스크 여유 | 256GiB / 20GiB |
| 기기 수 / 신규 기기 | 1000대 / KST 하루 100대 |
| 전체 작업 / 활성 작업 | 10000개 / 전체 64개·기기별 4개 |
| 상태 JSON 메타데이터 / 활성 lease | 64MiB / 10분 |
| 하루 수신 | 전체 128GiB·기기별 16GiB |
| 요청 | 분당 전체 2400회·기기별 600회 |
| 파일 / 작업 / manifest | 8GiB / 64GiB / 8MiB·2000파일 |
| 청크 / 동시 handler | 1MiB / 4개 |
| 유휴 읽기 / 전체 요청 읽기 | 10초 / 30초 |

누적량은 정상·partial·quarantine과 상태/영수증 파일을 포함하고 재시작 때 실제
디스크를 다시 집계합니다. 일일·분당 카운터는 원자 저장하며 날짜/분이 뒤로
가도 초기화하지 않습니다. 일일 바이트는 쓰기 전에 기록하므로 실패나 강제 종료
때문에 실제 저장량보다 보수적으로 계산될 수 있습니다. 한도에 걸리면 429 또는
507, 일시중지·handler 포화·요청 시간 초과는 503을 반환합니다. 429/503에는
`Retry-After`가 있습니다. 파일 다운로드나 전체 작업 목록 API는 제공하지 않습니다.

활성 작업은 10분 lease로 계산합니다. 동일 manifest POST 재접수와 성공한 청크
진전만 연장하고 GET은 연장하지 않습니다. 만료된 PUT은 409를 반환하므로 장치가
같은 manifest를 다시 접수한 뒤 이어받습니다. 완료 시 또는 owner 인증을 거친
`POST /jobs/<jobId>/release`의 `{version:1}` 요청 시 슬롯을 반납합니다. 반복·
미접수 release는 같은 응답이며 파일·partial·영수증은 지우지 않습니다. 장치가
영구적으로 사라져도 lease 만료 후 새 작업을 받을 수 있습니다. 상태 JSON은
시작 시 읽기 전에 합계 크기를 검사하고 증가분도 제한합니다.

폰 브라우저의 job 요청에는 양수 `X-Carrot-Transport-Epoch`와 32hex
`X-Carrot-Transport-Id`가 필수입니다. 같은 작업의 POST만 더 높은 epoch로
소유권을 바꾸며 이후 GET·PUT·release는 현재 epoch와 브라우저 ID가 정확히
맞아야 합니다. 이전 탭의 늦은 업로드·취소는 409로 차단하며 재시작 후에도
최대 epoch를 보존합니다. 직접 Python 시험용 메서드 기본값은 HTTP 정책과 별개입니다.

CORS는 검증한 LAN IPv4 웹캐럿 HTTP(S) origin만 정확히 echo합니다. 사용자정보·
경로·쿼리·fragment·null·와일드카드·일반 외부 호스트는 거부합니다. localhost와
loopback origin은 명시적 합성 시험 모드에서만 허용합니다. OPTIONS는 지정된
POST/GET/PUT와 인증·범위·기기·전송 fence 헤더만 허용하고 `Retry-After`를
브라우저에 노출합니다.

경로 탈출·Windows 예약명·symlink·junction·reparse를 거부합니다. 키 해시·상태·
영수증에는 제한적인 생성 모드를 적용하지만 Windows chmod는 사용자별 ACL
격리를 보장하지 않습니다. 본인 계정의 비공개 로컬 폴더를 사용하세요. 로컬
관리자나 동시에 파일을 바꾸는 다른 프로세스로부터의 격리 수단은 아닙니다.

## 검사와 연동

```powershell
python -m unittest discover -s tools/carrot_pc_logs/tests -p test_receiver.py -v
```

시험은 합성 파일·임시 폴더·loopback HTTP와 fake Serve 프로세스만 사용합니다.
실제 Serve 연결·권한 설정·폰 중계·차량 설치·주행 부하는 별도 검증 범위입니다.

연동 API는 `Receiver(state_root, output, endpoint=DEFAULT_ENDPOINT, policy=Policy())`와
`make_server(receiver, host="127.0.0.1", port=8766)`입니다. 합성 시험만
`allow_test_http=True`로 loopback endpoint를 주입할 수 있습니다. 이전 pull/
pairing 초안은 외부 스냅샷에 보존되며 활성 경로에는 포함하지 않습니다.
