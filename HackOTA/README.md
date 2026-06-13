# HackOTA 3-Device Lab

다음 세 파일을 각각 한 장비에 복사하여 실행하는 의도적으로 취약한 OTA
교육 환경입니다.

| 장비 | 복사할 파일 | 역할 |
| --- | --- | --- |
| Laptop | `web_app.py` | Web UI, Update Server, MQTT Publish, 결과 Dashboard |
| Raspberry Pi 1 | `central_gateway.py` | MQTT 수신, ECU 전달, 결과 중계 |
| Raspberry Pi 2 | `ecu.py` | Firmware 수신, A/B Slot 저장, 결과 전송 |

각 Python 파일은 HackOTA의 다른 Python 모듈을 필요로 하지 않는 독립형
파일입니다.

## 통신 구조

```text
Browser
   |
Laptop: web_app.py + Mosquitto
   |
   | MQTT TCP 1883
   v
Raspberry Pi 1: central_gateway.py
   |
   | UDP 17001
   v
Raspberry Pi 2: ecu.py
   |
   | UDP 17999
   v
Raspberry Pi 1 -> MQTT -> Laptop Dashboard
```

## 설치

### Laptop

```powershell
python -m pip install Flask paho-mqtt
```

Laptop에는 Mosquitto Broker도 실행되어야 합니다. 교육용 설정은
`mosquitto.conf`를 사용할 수 있습니다.

### Raspberry Pi 1

```bash
python3 -m pip install paho-mqtt
```

### Raspberry Pi 2

`ecu.py`는 Python 표준 라이브러리만 사용하므로 별도 패키지가 필요 없습니다.

## 예시 네트워크

```text
Laptop IP:        192.168.0.10
Gateway Pi IP:    192.168.0.20
ECU Pi IP:        192.168.0.30
MQTT Port:        1883
ECU UDP Port:     17001
Result UDP Port:  17999
```

세 장비의 방화벽에서 실습에 사용하는 TCP/UDP 포트를 허용해야 합니다.

## 실행 순서

### 1. Laptop

Mosquitto를 먼저 실행한 후:

```powershell
python web_app.py
```

입력 예:

```text
MQTT Broker IP [127.0.0.1]:
MQTT Broker Port [1883]:
MQTT Username [admin]:
MQTT Password [admin]:
Web Host [0.0.0.0]:
Web Port [5000]:
```

Laptop 내부 Broker를 사용하므로 Broker IP는 기본값을 사용합니다.

### 2. Raspberry Pi 2 ECU

```bash
python3 ecu.py
```

입력 예:

```text
ECU Name [powertrain]:
Listen Host [0.0.0.0]:
ECU UDP Port [17001]:
Central Gateway Raspberry Pi IP: 192.168.0.20
Gateway Result UDP Port [17999]:
```

### 3. Raspberry Pi 1 Central Gateway

```bash
python3 central_gateway.py
```

입력 예:

```text
MQTT Broker IP (Laptop IP): 192.168.0.10
MQTT Broker Port [1883]:
MQTT Username [gateway]:
MQTT Password [gateway]:
ECU Raspberry Pi IP: 192.168.0.30
ECU UDP Port [17001]:
Gateway Result UDP Port [17999]:
```

### 4. Web 접속

Laptop에서:

```text
http://127.0.0.1:5000
```

다른 교육용 PC에서 접속한다면:

```text
http://192.168.0.10:5000
```

## 생성되는 데이터

* Laptop: `hackota_uploads`
* ECU Raspberry Pi: `<ecu_name>_ecu_data`

이 디렉터리를 삭제하면 업로드 패키지와 ECU Slot 상태를 초기화할 수
있습니다.

## 의도적으로 제외된 보안

* Web 로그인, 역할 검증, CSRF
* 업로드 파일의 형식, 크기, 내용 검증
* MQTT TLS, 강한 인증, Topic ACL
* Firmware 전자서명 및 해시 검증
* Gateway의 Notice/File 결합 검증
* Gateway와 ECU 사이의 송신자 인증
* CAN Chunk 누락, 중복, 순서 검증
* ECU Anti-rollback 및 부팅 성공 확인

실제 차량 또는 외부 네트워크에 연결하지 말고 격리된 교육 환경에서만
사용합니다.
