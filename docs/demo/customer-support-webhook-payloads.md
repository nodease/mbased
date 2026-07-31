# 고객지원 티켓 웹훅 Payload

시연용 고객지원 티켓 처리 workflow에 바로 붙여 넣을 수 있는 요청 본문이다.

## 1. 팀원 초대 실패

```json
{
  "customerTier": "business",
  "message": "결제는 완료됐는데 팀원 초대가 계속 실패합니다. 초대 메일도 안 오고 있어요."
}
```

## 2. 프로젝트가 보이지 않음

```json
{
  "customerTier": "enterprise",
  "message": "어제까지 보이던 프로젝트가 오늘 로그인하니 사라졌습니다. 복구할 수 있나요?"
}
```

## 3. API 호출량 급증

```json
{
  "customerTier": "enterprise",
  "message": "API 호출량이 갑자기 평소보다 5배 높게 찍혔는데, 어떤 키에서 발생했는지 확인 부탁드립니다."
}
```

## 4. 문서 처리 지연

```json
{
  "customerTier": "business",
  "message": "파일 업로드 후 문서 처리가 처리 중 상태에서 30분째 멈춰 있습니다."
}
```

## 5. 청구서 정보 변경

```json
{
  "customerTier": "business",
  "message": "이번 달 사용 요금 청구서를 회사명 변경된 정보로 다시 발급받고 싶습니다."
}
```

## 6. SSO 로그인 오류

```json
{
  "customerTier": "enterprise",
  "message": "SSO 로그인을 설정했는데 일부 직원만 로그인되고 나머지는 권한 없음 오류가 납니다."
}
```

## 7. Slack 알림 미전송

```json
{
  "customerTier": "business",
  "message": "운영 중인 워크플로우가 실행은 성공으로 보이는데 Slack 알림이 전송되지 않았습니다."
}
```

## 8. Knowledge Base 문서 삭제

```json
{
  "customerTier": "business",
  "message": "실수로 Knowledge Base 문서를 삭제했는데, 삭제 이력이나 복원 기능이 있나요?"
}
```

## 9. SLA 장애 문의

```json
{
  "customerTier": "enterprise",
  "message": "엔터프라이즈 계약 기준 SLA가 적용되는 장애로 보입니다. 담당자와 예상 복구 시간을 알려주세요."
}
```

## 10. 해지 전 데이터 내보내기

```json
{
  "customerTier": "free",
  "message": "서비스 해지 전에 저장된 데이터와 실행 로그를 모두 내려받을 수 있는 방법이 필요합니다."
}
```
