# Organization Test Cases

Status: Draft

## Minimum Failure Rule

이 문서는 정상 시나리오를 길게 반복하지 않고, 각 organization/RBAC 조건을 깨뜨리는 최소 입력, 상태, 또는 관찰값을 기준으로 테스트 케이스를 정의한다.

각 테스트는 해당 최소 조건 하나만으로 실패를 유도하거나, 성공 경로의 필수 관찰값 하나가 빠졌을 때 실패로 판단할 수 있어야 한다.

다음 항목은 이 문서의 test case로 늘리지 않는다.

- 단순 성공 경로 반복, 표시 문구/색상/레이아웃 세부값
- 다른 feature가 소유한 credentials, knowledge, audit tab 내부 동작
- API spec의 모든 field를 다시 나열하는 schema mirror test
- 같은 정책을 endpoint별로 반복하는 중복 happy-path test

## Current Coverage Notes

현재 backend coverage는 `apps/gateway/tests/api/test_organizations_api.py`, `apps/gateway/tests/api/test_teams_api.py`, `apps/gateway/tests/api/test_permissions_api.py`, `apps/gateway/tests/services/test_organization_member_service.py`, `apps/gateway/tests/services/test_team_service_permissions.py`, `apps/shared/tests/services/test_permissions.py`, `apps/shared/tests/services/test_permission_enforcement.py`, `tests/db/test_organization_user_schema.py`, `tests/db/test_team_permission_constraints.py`, `tests/test_permission_schema.py`, `apps/shared/tests/test_organization_membership_schema.py`에 분산되어 있다.

현재 client coverage는 active organization을 소비하는 knowledge/workflow 일부 테스트와 권한 신청 제출 wrapper(`organizationApi.test.ts`), Sidebar notification overlay 테스트를 포함한다. AdminConsolePage의 member/team/permission 관리 UI에 대한 직접 component test는 확인되지 않았다. App 생성 권한 신청 UI(ORG-TC-E009~E013)는 `apps/client/app/features/app/components/create-app-modal/index.test.tsx`가 담당한다.

ORG-TC-U032는 ADR-0070 Target이며 MBA-362가 privacy policy schema/bootstrap을 구현하기 전에는
현재 foundation 동작의 완료 증거가 아니다.

## Unit Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| ORG-TC-U001 | 기본 organization foundation은 organization, manager membership, Default team, team membership을 함께 만들어야 한다. | 신규 user에 대해 네 요소 중 하나가 없다. | 테스트 실패. |
| ORG-TC-U002 | 기본 organization foundation은 기존 active organization이 있으면 중복 생성하지 않아야 한다. | active membership이 이미 있는데 새 organization이 추가된다. | 기존 organization id 반환. |
| ORG-TC-U003 | active organization 목록은 active membership만 사용해야 한다. | invited/suspended/removed membership organization이 결과에 포함된다. | 테스트 실패. |
| ORG-TC-U004 | membership summary는 active/invited만 반환해야 한다. | invited membership이 빠지거나 removed/suspended membership이 포함된다. | 테스트 실패. |
| ORG-TC-U005 | member list 기본값은 removed를 제외하고, `state=removed`는 removed만 조회해야 한다. | 기본 조회에 removed가 포함되거나 removed 조회가 빈 목록이다. | 테스트 실패. |
| ORG-TC-U005a | member list의 이번 달 비용은 current membership state가 아니라 eligible usage의 `user_id`를 기준으로 합산해야 한다. | invited/suspended/removed member의 당월 usage가 0으로 숨겨지거나, usage가 없는 member가 0 이외 비용을 받거나, 다른 organization usage가 섞인다. | 각 member의 `current_month_usage`는 당월 사용자별 비용 합계이며, usage가 없는 member만 모든 비용 0이다. |
| ORG-TC-U005b | Durable provider usage는 user형 execution subject에 귀속하고 credential/billing/audit principal을 member user로 대체하지 않아야 한다. | Public/system 비용이 creator에게 붙거나 credential principal에게 실제 실행 사용자 비용이 잘못 귀속된다. | Canonical success는 execution subject에만 한 번 합산되고 projection은 중복 제외된다. Unresolved user operation이 없으면 `usage_data_complete=true`, `unresolved_provider_call_count=0`이고, 남아 있으면 completeness false와 정확한 count를 표시한다. |
| ORG-TC-U006 | organization auth state는 `member` 또는 `manager`만 허용해야 한다. | `owner`, `admin`, `viewer`가 organization auth state로 통과한다. | 테스트 실패. |
| ORG-TC-U007 | member invitation은 자기 자신 초대와 잘못된 재초대 상태 전이를 거부해야 한다. | self invite가 성공하거나 suspended member가 invitation으로 invited가 된다. | `400` 또는 `409`. |
| ORG-TC-U008 | removed member 재초대는 기존 membership을 invited로 되살려야 한다. | 새 duplicate membership을 만들거나 removed 상태가 유지된다. | 기존 row의 state가 invited로 변경된다. |
| ORG-TC-U008a | invited member의 초대 거절은 membership을 removed로 바꾸고 audit을 기록해야 한다. | 거절 후 invited가 유지되거나 `organization.member.decline` audit이 없다. | state `removed`, audit 기록. |
| ORG-TC-U008b | notification service는 invited membership만 organization invitation 알림으로 파생해야 한다. | active/suspended/removed membership이 알림으로 표시된다. | invited organization만 `organization.invitation` 반환. |
| ORG-TC-U008c | 관리자가 invited member를 제거하면 commit 이후 대상 사용자 notification을 갱신해야 한다. | commit 전/실패 후 발행하거나 active/suspended/already removed 제거에도 발행하거나 publish 실패가 제거를 rollback한다. | invited 제거 commit 후 대상 user channel에 한 번 발행, commit 실패와 non-invited 제거에는 미발행, publish 실패는 committed 제거 유지. |
| ORG-TC-U009 | invited/removed member는 PATCH로 active/suspended 전환할 수 없어야 한다. | invited 또는 removed member update가 성공한다. | `409`. |
| ORG-TC-U010 | member update는 빈 update와 no-op audit을 구분해야 한다. | 빈 body가 성공하거나 no-op PATCH가 audit row를 만든다. | 빈 body는 `400`, no-op은 audit 없음. |
| ORG-TC-U011 | 자기 자신 또는 마지막 active manager의 상태/권한 변경은 거부해야 한다. | self update 또는 마지막 manager 강등/제거가 성공한다. | `400` 또는 `409`. |
| ORG-TC-U011a | last active manager count는 globally deactivated manager-role membership을 제외해야 한다. | 실제 active manager 1명 + deactivated manager-role 1명에서 실제 manager 강등이 성공한다. | 409, globally active manager 1명 이상 유지. |
| ORG-TC-U012 | member removal은 team membership, user direct permission, App 생성 권한 row를 정리해야 한다. | removed 처리 후 team membership, user workflow/Knowledge Base/LLM direct permission, `user_app_creation_permissions` row 중 하나가 남는다. | cleanup count와 삭제가 일치한다. |
| ORG-TC-U013 | 이미 removed인 member removal은 idempotent해야 한다. | removed member DELETE가 404 또는 409를 반환한다. | `status=removed`, cleanup count 0. |
| ORG-TC-U014 | team mutation은 organization manager scope 안에서만 수행되어야 한다. | scope 밖 user나 non-manager가 team create/update/member mutation에 성공한다. | `404` 또는 `403`. |
| ORG-TC-U015 | team `managed_by`와 team member add는 active organization user만 허용해야 한다. | scope 밖, inactive, membership 없는 user가 저장된다. | `400` 또는 `404`. |
| ORG-TC-U016 | permission grant는 active team 또는 active organization member만 grantee로 허용해야 한다. | inactive team이나 inactive/scope 밖 user에게 permission row가 생성된다. | `400` 또는 `404`. |
| ORG-TC-U017 | effective resource permission은 additive allow와 fail-closed를 지켜야 한다. | user direct가 team 권한을 낮추거나 invalid/audit-only auth_state가 operational permission을 허용한다. | 가장 강한 유효 권한 또는 deny. |
| ORG-TC-U018 | actor access policy는 audit visibility와 organization mutation authority를 분리해야 한다. | auditor-only user가 profile 또는 action을 수행한다. | 403. |
| ORG-TC-U019 | suspension은 stored grant를 보존하면서 effective access와 latent privilege 증가를 차단해야 한다. | suspend가 team/direct/App row를 삭제하거나 suspended member의 promotion/add/grant가 성공한다. | row 보존, effective access none, reactivation/cleanup 외 state-changing action 409. |
| ORG-TC-U020 | active manager-override target의 resource mutation은 silent no-effect로 성공하지 않아야 한다. | manager override 상태에서 direct/team/App revoke가 applied로 반환된다. | 409 `manager_override_active`. |
| ORG-TC-U021 | direct restore는 explicit canonical re-grant여야 한다. | AuditLog 또는 deleted row id에서 auth_state를 자동 복원한다. | manager가 선택한 auth_state로 새 grant/upsert. |
| ORG-TC-U022 | access action audit 실패는 mutation을 rollback해야 한다. | permission/membership row는 바뀌었지만 AuditLog add/flush가 실패한다. | mutation과 audit 모두 미커밋. |
| ORG-TC-U023 | access action no-op은 audit을 만들지 않아야 한다. | same state/role/auth_state 또는 existing team add가 canonical audit을 추가한다. | unchanged, audit 없음. |
| ORG-TC-U024 | manager override는 active membership + manager role에만 적용되어야 한다. | suspended manager-role target의 cleanup revoke가 override conflict로 막히거나 effective manager로 계산된다. | override false, stored source 표시, effective none. |
| ORG-TC-U025 | legacy direct `none` row는 allow source로 계산하지 않아야 한다. | none row가 effective/count allow를 높이거나 actor grant가 새 none row를 만든다. | inert row 표시/회수 가능, 신규 none은 422. |
| ORG-TC-U026 | management reason은 durable 저장 전에 fail-closed sanitize되어야 한다. | secret/PII가 raw reason으로 저장되거나 sanitizer 실패 뒤 mutation이 commit된다. | redacted reason 또는 전체 rollback. |
| ORG-TC-U027 | notification projection은 invitation과 Security Alert source를 섞지 않아야 한다. | `GET /notifications` item에 Security Alert가 들어가거나 summary가 invitation membership을 반환한다. | Invitation endpoint는 invited membership만, Security Alert summary는 별도 source. |
| ORG-TC-U028 | Sidebar Security Alert badge는 open만 세야 한다. | acknowledged/resolved alert가 badge에 포함된다. | Manager summary의 open count만 badge 표시. |
| ORG-TC-U029 | Workflow user direct permission mutation은 중앙·legacy 경로 모두 access subject → App lifecycle → permission scope 순서를 사용하고 primary 교체를 재검증해야 한다. | 한 경로는 subject를 잡고 App을 기다리며 다른 경로는 App을 잡고 subject를 기다리거나, old primary만 변경하고 성공한다. | Deadlock 경로 없음. Primary 변경 시 `409 workflow.primary_changed`, permission/audit 불변. |
| ORG-TC-U030 | 멤버 제거와 primary Workflow 권한 승계가 겹치면 제거 transaction이 승계된 target grant까지 회수해야 한다. | `FOR UPDATE` subject lock과 permission FK `KEY SHARE`가 scope 대기와 순환해 deadlock 나거나, 새 primary grant가 제거 후 남는다. | FK-compatible subject lock을 유지한 채 Workflow scope 집합을 재조회·잠그고 old/target direct grant를 모두 삭제. |
| ORG-TC-U031 | Resource bulk grant는 최대 50개 resource×grantee pair와 row별 audit을 한 transaction에서 적용해야 한다. | 두 번째 pair의 scope, active state, lifecycle, audit 또는 persistence가 실패했는데 첫 pair이 commit된다. | Permission/audit 전체 rollback, partial success 없음. |
| ORG-TC-U032 | Target foundation은 current platform contract/validity revision을 참조하고 null provider의 explicit initial `baseline_only` privacy policy와 positive initial Organization validity revision/epoch을 organization/membership/Default team과 같은 Unit of Work에 생성해야 한다. Review readiness가 없으면 action policy가 `manual_review`를 만들 수 없어야 한다. | Policy/validity insert·flush·commit 실패 뒤 foundation 일부가 남거나, idempotent retry가 duplicate policy/epoch을 만들거나, missing policy/validity를 runtime implicit default로 보충하거나, signup/OAuth input/response가 provider/mode/revision/validity scope/epoch을 override·노출한다. | Foundation/policy/validity 전체 원자 rollback 또는 exact 기존 foundation/policy/current Organization validity 반환, duplicate/implicit default/override/projection 0건. |
| ORG-TC-U033 | Target rollout은 모든 Organization writer의 privacy dual-write 수렴과 구버전 writer fence 뒤 backfill/rescan 및 no-default DB foundation constraint를 완료해야 한다. | 최초 scan 직후 구버전 writer가 Organization을 만들거나 new writer create와 backfill이 경합했는데 final zero-missing/constraint 없이 enforcement가 켜지거나, enforcement 뒤 구버전 image가 시작 또는 직접 insert한다. | 새 Organization은 creation UoW 또는 final rescan 중 하나로 정확히 한 policy/validity set에 수렴한다. Missing row/marker/ref가 있으면 activation zero-write이고, old image startup/rollback은 readiness failure, 우회 insert는 Organization/foundation partial row 0건의 DB rollback이다. |

## API Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| ORG-TC-A001 | organization scope endpoint는 `X-Organization-Id` header를 요구해야 한다. | header 없이 scope endpoint를 호출한다. | `400`, `organization.required`. |
| ORG-TC-A002 | organization header와 UUID path/body 값은 형식을 검증해야 한다. | `X-Organization-Id=not-a-uuid` 또는 malformed UUID를 보낸다. | `422`, `validation.failed`. |
| ORG-TC-A003 | path organization과 header organization mismatch는 숨겨야 한다. | path id와 header id가 다르다. | `404`, `resource.not_found`. |
| ORG-TC-A004 | scope 밖 organization/resource/team/user는 숨겨야 한다. | active membership이 없는 organization 또는 다른 organization resource를 요청한다. | `404`, `resource.not_found`. |
| ORG-TC-A005 | scope 안 non-manager는 manager API를 사용할 수 없어야 한다. | active member가 organization/team/member manager endpoint를 호출한다. | `403`, `permission.denied`. |
| ORG-TC-A006 | organization PATCH는 빈 update, blank name, null options를 거부해야 한다. | `{}`, blank `name`, 또는 null `options`를 보낸다. | `400`, `validation.failed`. |
| ORG-TC-A007 | member list는 invalid state filter를 거부해야 한다. | `?state=unknown`. | `400`, `Invalid membership state.` |
| ORG-TC-A007a | member list 비용 응답은 App primary workflow와 organization scope를 지키며, legacy NULL usage만 포함해야 한다. | non-primary workflow 또는 명시적 다른 organization usage가 member 비용에 합산된다. | `total_cost = workflow_execution_cost + agent_builder_cost`, 타 organization usage 제외. |
| ORG-TC-A008 | member invite/update request는 unknown body field를 거부해야 한다. | body에 정의되지 않은 field를 추가한다. | `422` validation envelope. |
| ORG-TC-A009 | `/members/me/accept`는 literal `me` route로 처리되어야 한다. | `/members/me/accept`가 `{user_id}` route로 해석된다. | 테스트 실패. |
| ORG-TC-A009a | `/members/me/decline`은 literal `me` route로 처리되어야 한다. | `/members/me/decline`이 `{user_id}` route로 해석된다. | 테스트 실패. |
| ORG-TC-A010 | member removal response는 cleanup summary를 포함해야 한다. | 성공 응답에서 `removed_team_memberships` 또는 `revoked_user_permissions`가 빠진다. | 테스트 실패. |
| ORG-TC-A011 | team list는 invalid limit을 거부해야 한다. | `limit=0`, `limit=101`, 또는 non-integer. | `422`, `validation.failed`. |
| ORG-TC-A012 | team create/update는 duplicate name과 blank name을 거부해야 한다. | duplicate create 또는 blank name patch가 성공한다. | `409` 또는 `400`. |
| ORG-TC-A013 | team member add/remove/deactivate는 idempotent 조건을 지켜야 한다. | existing add, missing remove, inactive deactivate 중 하나가 error를 반환한다. | success 응답. |
| ORG-TC-A014 | permission list는 team/user direct entries를 분리해야 한다. | user direct permission이 `team_permissions`에 섞인다. | 테스트 실패. |
| ORG-TC-A015 | permission PUT은 organization manager 또는 target resource manager만 허용해야 한다. | manage 권한 없는 active member가 permission을 저장한다. | `403`, `permission.denied`. |
| ORG-TC-A016 | permission PUT은 canonical auth_state만 허용해야 한다. | `{ "auth_state": "admin" }` 또는 audit-only value가 통과한다. | `422`, `validation.failed`. |
| ORG-TC-A017 | permission DELETE는 missing row를 숨기고, existing direct row는 target user active 여부와 무관하게 회수해야 한다. | missing row가 success거나 deactivated/removed user의 existing direct row 삭제가 실패한다. | `404` 또는 permission row 삭제. |
| ORG-TC-A018 | 권한 신청 제출 wrapper는 `app.create`와 신청 사유를 보내야 한다. | `requested_permission`이 빠지거나 `reason`이 변형되어 전송된다. | `POST /permission-requests` payload가 `{ requested_permission: "app.create", reason }`이다. |
| ORG-TC-A019 | `GET /notifications`는 현재 user의 초대 알림 목록을 반환해야 한다. | 다른 user 또는 non-invited membership이 포함된다. | `{ items: [...] }` 안에 현재 user invited만 포함. |
| ORG-TC-A020 | member access profile은 manager와 same-org membership을 요구하고 unbounded team list를 포함하지 않아야 한다. | auditor-only/cross-org target으로 profile이 반환되거나 team row 전체가 profile에 포함된다. | 403/404 또는 team count-only profile. |
| ORG-TC-A021 | resource access list는 direct/team source와 effective auth_state를 분리하고 paginate해야 한다. | team source에 remove precondition용 membership id가 없거나 source가 섞이거나 stable tie-break 없이 page가 중복/누락된다. | `team_membership_id` 포함 schema와 stable distinct-resource pagination 통과. |
| ORG-TC-A022 | access action discriminator는 action별 필수/금지 field를 검증해야 한다. | revoke에 auth_state가 들어가거나 grant에 resource_id가 없다. | 422. |
| ORG-TC-A023 | access action reason은 JSON body에서 blank/null/길이/control 문자를 검증해야 한다. | query reason을 사용하거나 500자 초과/control 문자가 저장된다. | normalized null 또는 422. |
| ORG-TC-A024 | cross-org actor/resource/team id는 존재를 숨겨야 한다. | 다른 organization 이름/id가 response 또는 error detail에 노출된다. | 404 `resource.not_found`. |
| ORG-TC-A025 | concurrent last-two-manager mutation은 하나만 성공해야 한다. | 두 manager가 동시에 suspended/member가 되어 active manager가 0명이 된다. | 하나 성공, 하나 409. |
| ORG-TC-A026 | concurrent permission/App revoke는 canonical audit을 정확히 한 번 기록해야 한다. | 두 요청이 success하거나 audit이 중복된다. | 하나 applied, 나머지 404, audit 1건. |
| ORG-TC-A027 | stale actor action은 다른 manager의 최신 변경을 덮어쓰지 않아야 한다. | profile 이후 role/auth-state/row id가 바뀌었는데 이전 expected state action이 성공한다. | 409 `stale_state`, mutation/row-level audit 없음, safe `policy.block` 1건. |
| ORG-TC-A028 | 모든 action variant는 common user/membership snapshot과 action별 row precondition을 검증해야 한다. | user active/membership id/state/role 누락, create/update precondition 혼합, 다른 variant field가 통과한다. | 422 `validation.failed`. |
| ORG-TC-A029 | desired-state no-op과 destructive missing/stale 판정 순서를 구분해야 한다. | membership/user identity mismatch가 no-op으로 숨겨지거나 same-row retry가 stale이거나 missing direct/App revoke가 unchanged이거나 row ABA가 적용된다. | identity/global state 및 ABA mismatch는 409, same-row/same-value retry와 team remove missing은 unchanged, missing direct/App revoke는 404. |
| ORG-TC-A030 | actor profile target은 active/suspended membership으로 제한해야 한다. | invited/removed/missing actor profile이 반환된다. | 404 `resource.not_found`. |
| ORG-TC-A031 | manager caller 판정은 membership-first legacy fallback을 지켜야 한다. | membership 있는 non-manager legacy owner가 fallback으로 허용되거나 membership 없는 legacy owner가 기존 정책과 달리 거부된다. | ADR-0009 판정과 일치. |
| ORG-TC-A032 | reason normalization과 redaction은 server 기준으로 적용되어야 한다. | CRLF/trim/Unicode 길이, forbidden control/bidi, common secret/PII, sanitizer failure 중 하나가 raw 저장된다. | normalized/redacted 또는 422/rollback. |
| ORG-TC-A033 | last-manager와 일반 access action은 canonical lock order를 지켜야 한다. | 서로 다른 manager target을 먼저 lock한 뒤 manager set lock으로 deadlock 나거나 legacy route가 lock을 우회한다. | active-manager id-order 또는 target/resource/child order, invariant 유지. |
| ORG-TC-A034 | action response는 row 변경과 effective access 변경을 구분해야 한다. | direct revoke가 applied이고 stronger team source가 남는데 `effective_access_changed=true`다. | `status=applied`, `effective_access_changed=false`. |
| ORG-TC-A034a | team membership action response는 multi-resource source impact를 단일 effective boolean으로 축약하지 않아야 한다. | team remove가 source count를 exact effective loss로 표시하거나 `effective_access_changed=true/false`로 단정한다. | effective field null, transaction-observed advisory source count 반환, UI 재조회. |
| ORG-TC-A035 | actor access policy block과 permission denial은 canonical failure audit을 구분해야 한다. | self/last-manager/stale가 row-level mutation action으로 기록되거나 non-manager 403이 policy.block으로 기록된다. | `policy.block` 또는 `permission.denied`, safe reason metadata. |
| ORG-TC-A036 | hidden/validation/no-op은 actor access audit side channel을 만들지 않아야 한다. | cross-org 404, malformed 422, unchanged가 target-aware audit을 남긴다. | actor access audit 없음. |
| ORG-TC-A037 | policy-block audit 실패는 원래 400/409를 성공처럼 반환하지 않아야 한다. | block audit commit 실패 뒤 원래 conflict만 반환하거나 mutation이 생긴다. | 500, mutation 없음. |
| ORG-TC-A038 | access-management package는 ADR-0022 import/composition 경계를 지켜야 한다. | application이 FastAPI/SQLAlchemy/concrete adapter를 import하거나 router가 query/commit한다. | static boundary test 통과, composition root는 application 밖. |
| ORG-TC-A039 | legacy route 위임은 기존 authorization과 latent-row 정책을 보존해야 한다. | resource manager가 actor manager-only guard로 403이 되거나 active manager target의 latent row/team mutation이 actor-only override block으로 409가 된다. | 기존 authorization/status/policy 유지, mutation coordinator만 공유. |
| ORG-TC-A040 | globally deactivated target은 cleanup-only profile/action을 제공해야 한다. | effective access/manager override가 enabled이거나 reactivate/promotion/add/grant가 성공하거나 revoke/remove가 막힌다. | effective none, cleanup 허용, privilege increase 409. |
| ORG-TC-A041 | target User active snapshot과 row lock은 actor action의 global-state snapshot을 안정화해야 한다. | profile 뒤 global state가 먼저 바뀌었는데 이전 action이 no-op 또는 applied로 처리된다. | 409 stale_state + policy.block, mutation 없음. Actor action commit 뒤 별도 lifecycle 변경은 이 계약 밖. |
| ORG-TC-A042 | last-manager lock은 membership과 corresponding User row를 두 단계 안정 순서로 잠가야 한다. | membership/User lock 순서가 요청마다 달라 deadlock 나거나 lock 뒤 global eligibility를 다시 계산하지 않는다. | membership id 순 lock, 대응 User same-order lock, actor action 시점 invariant 유지. |
| ORG-TC-A043 | role control과 policy는 promotion/demotion 방향을 구분해야 한다. | last/self/suspended/globally inactive target에 하나의 role boolean을 적용해 허용된 demotion이 막히거나 promotion이 열린다. | `member`/`manager` desired control 분리, server policy와 일치. |
| ORG-TC-A044 | inactive team membership은 cleanup과 effective source를 구분해야 한다. | inactive team이 effective permission/count 또는 add catalog에 포함되거나 stored membership을 정리할 수 없다. | profile에는 cleanup row 표시 가능, effective/add 제외, remove 허용. |
| ORG-TC-A045 | version 없는 current-state ABA 정책을 명시적으로 지켜야 한다. | same row가 expected/desired value로 돌아온 요청을 무조건 stale로 보거나, 다른 row id로 재생성된 ABA를 unchanged로 본다. | same-row value-only ABA는 current-state semantics, row-id ABA는 409, 중간 변경은 audit 보존. |
| ORG-TC-A046 | inert direct/team permission state의 actor projection을 구분해야 한다. | user-direct none이 cleanup에서 사라지거나 team none/audit-only row가 effective source/count/projection을 만든다. | direct none은 inert cleanup row, team non-operational state는 actor projection 제외. |
| ORG-TC-A047 | access-management port는 capability별 최소·framework-independent 계약이어야 한다. | 하나의 repository protocol이 read projection, 모든 mutation, audit, transaction을 모두 노출하거나 ORM object/listener token이 application port를 통과한다. | query/membership/team/direct/App/resource-scope/audit/UoW port 분리, pure mutation descriptor 사용, concrete adapter 다중 구현은 허용. |
| ORG-TC-A048 | member team membership 목록은 active/inactive row와 inherited impact를 안정적으로 paginate해야 한다. | join duplicate/불안정 정렬로 page가 중복·누락되거나 item별 count N+1이 발생하거나 inactive row가 effective source/add 대상이 된다. | `{total,items}`, team name/id stable order, batched type별 distinct count, inactive count 0/cleanup-only. |
| ORG-TC-A049 | expected-absent retry는 같은 natural key와 desired value일 때만 unchanged여야 한다. | Existing direct row가 다른 auth state인데 expected-absent grant가 unchanged 또는 overwrite로 처리된다. | 409 `stale_state`; same desired team/direct/App row만 unchanged. |
| ORG-TC-A050 | Policy와 no-op의 판정 우선순위는 identity/global-state/row-id stale을 약화하지 않아야 한다. | Globally inactive 또는 manager-override target의 desired-state retry가 policy block이 되거나, membership/user-active/row-id mismatch가 no-op으로 숨겨진다. | same desired retry는 unchanged, identity/global-state/row-id mismatch는 먼저 409 stale. |
| ORG-TC-A051 | Paginated catalog 선택은 exact team/resource source 조회로 precondition을 구성해야 한다. | Current page 밖 existing team/direct row 또는 조회 실패를 absent로 오판해 create confirm을 연다. | `teamId`/`resourceId` exact 조회 0/1 결과로 row id/auth-state 또는 absence를 구성하고 mutation에서 재검증. 조회 실패는 action disabled + 명시적 retry. |
| ORG-TC-A052 | 404/409 이후 stale confirm payload를 다시 제출할 수 없어야 한다. | Profile을 갱신해도 열린 dialog가 이전 expected snapshot을 유지해 재제출한다. | Dialog 닫기, 최신 profile/source 조회, 사용자 재선택, 자동 재시도 없음. |
| ORG-TC-A053 | invitation endpoint는 Security Alert payload를 반환하지 않아야 한다. | `/notifications`가 alert detail/evidence를 섞어 반환한다. | 기존 invitation response shape 유지. |
| ORG-TC-A054 | Security Alert summary는 current organization owner/manager만 조회해야 한다. | 일반 member가 summary를 받거나 manager가 타 조직 summary를 받는다. | 403 또는 scope-safe 결과, cross-org 노출 없음. |

## E2E Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| ORG-TC-E001 | dashboard 진입은 organization이 하나뿐이면 자동 선택하고 여러 개면 선택 화면을 보여야 한다. | 단일 organization인데 선택 화면이 유지되거나 복수 organization인데 임의 선택된다. | 자동 저장 또는 `작업 조직 선택` 표시. |
| ORG-TC-E002 | active organization 선택 후 API 요청은 organization header를 보내야 한다. | 선택 후 `/organizations/current` 요청에 header가 없다. | 테스트 실패. |
| ORG-TC-E003 | Sidebar와 AdminConsolePage는 non-manager에게 관리 표면을 숨겨야 한다. | `is_manager=false`인데 관리 nav 또는 관리 테이블이 보인다. | 관리 nav 숨김, `관리 권한 없음` 표시. |
| ORG-TC-E004 | member invite/update/remove UI는 필요한 payload와 confirm gate를 지켜야 한다. | invite payload가 비었거나 privilege/destructive action이 confirm 없이 호출된다. | API 미호출 또는 confirm 후 호출. |
| ORG-TC-E005 | self 또는 마지막 manager action은 정지/강등/제거를 막아야 한다. | 자기 자신 row의 `정지` button 또는 자기 자신/마지막 manager row의 강등/제거 button이 활성화된다. | disabled button이며 클릭해도 member update/remove API를 호출하지 않는다. |
| ORG-TC-E006 | inactive team detail은 member add control을 숨겨야 한다. | inactive team에서 `추가` button이 활성화된다. | `비활성 팀에는 멤버를 추가할 수 없습니다.` 표시. |
| ORG-TC-E007 | permission tab은 resource와 active grantee 없이는 grant를 막아야 한다. | workflow/credential id 없거나 inactive team/member로 PUT 요청이 나간다. | save disabled 또는 후보 제외. |
| ORG-TC-E008 | active organization 변경 event 후 Sidebar는 organization name/manager flag를 새로 조회해야 한다. | event dispatch 후 이전 organization 이름이 유지된다. | `/organizations/current` 재호출. |
| ORG-TC-E009 | App 생성 `403` 차단은 일반 실패 토스트 대신 권한 신청 UI로 전환해야 한다. | `POST /apps` `403`에서 `앱 생성에 실패했습니다.` 토스트가 뜨거나 권한 신청 폼이 보이지 않는다. | 권한 신청 폼 표시, 실패 토스트 없음. |
| ORG-TC-E010 | 권한 신청 제출은 `app.create` 고정과 blank 아닌 신청 사유를 보내야 한다. | blank 사유로 API가 호출되거나 `requested_permission`이 `app.create`가 아니다. | blank 사유는 미호출 + 안내, 제출 body `{ requested_permission: 'app.create', reason }`. |
| ORG-TC-E011 | 권한 신청 `201` 성공은 신청 완료 안내를 표시해야 한다. | 성공 후 완료 안내 없이 form이 유지된다. | 신청 완료 안내 표시. |
| ORG-TC-E012 | 권한 신청 `409`는 detail에 따라 이미 권한 보유와 pending 중복 안내를 구분해야 한다. | 두 `409` detail이 같은 일반 오류 메시지로 표시된다. | `App creation permission already granted`는 보유 안내, `Pending permission request already exists`는 대기 안내. |
| ORG-TC-E013 | `403`이 아닌 App 생성 실패는 기존 실패 처리를 유지해야 한다. | duplicate name `400` 또는 일반 오류에서 권한 신청 UI로 전환된다. | 기존 실패 토스트 유지, 권한 신청 폼 없음. |
| ORG-TC-E014 | Sidebar 사용자 프로필 드롭다운은 알림 overlay를 열 수 있어야 한다. | `알림` item이 없거나 클릭해도 overlay가 열리지 않는다. | notification overlay 표시. |
| ORG-TC-E015 | 초대 알림 overlay는 organization 초대 수락/거절을 처리해야 한다. | `수락`/`거절` 클릭 시 해당 API가 호출되지 않거나 목록을 갱신하지 않는다. | accept/decline 호출 후 `GET /notifications` 재조회. |
| ORG-TC-E016 | `notifications.changed` SSE event는 알림 목록 재조회를 트리거해야 한다. | event 수신 후 기존 알림 목록이 유지된다. | `GET /notifications` 재호출. |
| ORG-TC-E017 | notification SSE 응답은 no-buffer header를 내려야 한다. | `X-Accel-Buffering: no` 또는 `Cache-Control: no-transform`이 없다. | `text/event-stream`과 no-buffer header 반환. |
| ORG-TC-E018 | Sidebar organization switcher는 현재 organization과 소속 구분을 표시해야 한다. | 현재 organization 이름 또는 `내 조직`/`멤버 조직` badge가 없다. | 현재 organization 이름과 구분 badge 표시. |
| ORG-TC-E019 | Sidebar organization switcher는 active organization 목록을 dropdown으로 전환할 수 있어야 한다. | organization이 2개 이상인데 dropdown이 열리지 않거나 선택 시 active organization이 저장되지 않는다. | dropdown 표시, 선택 item 저장, `/dashboard` 이동. |
| ORG-TC-E020 | Dashboard home은 active organization 변경 event를 받으면 데이터를 재조회해야 한다. | organization 전환 후 dashboard home이 이전 organization 데이터를 유지한다. | `nodease-active-organization-changed` 수신 후 dashboard home 재조회. |
| ORG-TC-E021 | audit actor button과 audit row click은 서로 다른 drawer를 열어야 한다. | actor click이 row detail까지 함께 열거나 propagation을 막지 못한다. | actor access drawer만 열림. |
| ORG-TC-E022 | actor drawer는 manager이고 관리 가능한 user actor일 때만 control을 제공해야 한다. | auditor/system/null/historical actor에 mutation button이 보인다. | audit detail만 가능, control 없음. |
| ORG-TC-E023 | actor access action은 confirm과 optional reason을 거쳐 한 항목만 전송해야 한다. | confirm 없이 호출하거나 한 request에 여러 mutation이 포함된다. | single discriminated action body. |
| ORG-TC-E024 | suspended member drawer는 stored grant와 effective disabled를 함께 표시해야 한다. | grant가 사라져 보이거나 effective access가 enabled로 보인다. | source 보존, effective disabled. |
| ORG-TC-E025 | direct revoke 후 team source가 남으면 effective access가 유지됨을 표시해야 한다. | UI가 access 완전 회수로 표시한다. | remaining team source/effective state 표시. |
| ORG-TC-E026 | manager override target은 role 강등 전 resource control이 비활성화되어야 한다. | 무효한 revoke action을 제출할 수 있다. | disabled + server 409 방어. |
| ORG-TC-E027 | stale actor action은 자동 재시도하지 않아야 한다. | 409 뒤 이전 payload를 다시 보내 최신 상태를 덮어쓴다. | profile refresh 후 새 confirm 필요. |
| ORG-TC-E028 | actor action payload는 drawer snapshot precondition을 포함해야 한다. | membership id/state/role 또는 source row precondition 없이 API를 호출한다. | client test 실패/API 422. |
| ORG-TC-E029 | manager notification overlay는 보안 알림과 조직 초대를 독립 section으로 표시해야 한다. | 한 source 실패로 전체 overlay가 사라지거나 alert item에 초대 action이 보인다. | Source별 상태와 action 분리. |
| ORG-TC-E030 | 일반 member는 Security Alert summary를 요청하거나 badge를 보면 안 된다. | manager false인데 summary 호출/cache badge가 남는다. | Invitation만 유지, Security Alert section/badge 없음. |
| ORG-TC-E031 | Security Alert item은 Admin deep link로 이동해야 한다. | Overlay 안에서 lifecycle mutation을 하거나 잘못된 tab으로 이동한다. | Overlay close 후 `tab=security-alerts&alertId=<uuid>` 이동. |
| ORG-TC-E032 | active organization 전환과 manager 권한 회수는 alert cache를 제거해야 한다. | 이전 조직 badge/detail이 새 scope에 남는다. | 이전 summary 제거 후 권한 있는 새 scope만 재조회. |
| ORG-TC-E033 | SettingsPage는 역할별 Access/LLM Credentials tab만 유지하고 Activity를 제거해야 한다. | manager/member에게 잘못된 tab이 보이거나 초기 load, refresh, permission grant에서 `/users/me/audit-logs`를 요청한다. | manager는 Access/LLM Credentials, member는 LLM Credentials만 표시하고 Activity UI와 audit 요청은 없다. |
| ORG-TC-E034 | Admin 상위 tab은 별도 `멤버`, `팀` 대신 `조직 구성` 하나를 제공해야 한다. | 상위 tab bar에 `멤버` 또는 `팀`이 별도 항목으로 남거나 `조직 구성`이 없다. | `조직 구성` 하나만 표시. |
| ORG-TC-E035 | 조직 구성의 기본 보기는 member 목록이어야 한다. | `view`가 없거나 유효하지 않을 때 team 또는 빈 화면이 표시된다. | member 보기 선택, member 목록 전체 너비 표시. |
| ORG-TC-E036 | 조직 구성 deep link는 `view=members|teams` 선택을 복원해야 한다. | 새로고침 또는 history 이동 뒤 다른 보기가 선택된다. | canonical URL의 보기와 선택 상태/목록이 일치. |
| ORG-TC-E037 | 기존 `tab=members|teams` deep link는 대응하는 조직 구성 URL로 정규화해야 한다. | legacy URL이 빈 화면, 기본 member 오판 또는 별도 legacy tab을 표시한다. | `tab=organization-structure&view=<legacy-value>`로 교체하고 같은 목록 표시. |
| ORG-TC-E038 | member/team 보기 전환은 두 목록을 동시에 렌더링하거나 각 보기의 목록 상태를 초기화하지 않아야 한다. | 좌우/상하로 두 목록이 동시에 보이거나 전환 후 검색/filter/page가 초기화된다. | 선택한 목록 하나만 전체 너비로 표시하고 보기별 상태 유지. |
| ORG-TC-E039 | 조직 구성 전환 control은 작은 화면에서도 overflow 없이 compact하게 표시되고 keyboard focus와 선택 상태를 제공해야 한다. | control이 화면 전체를 불필요하게 늘리거나 잘리며, button focus 또는 `aria-pressed` 상태가 없다. | 내용 너비의 동일 column button, native keyboard focus, 선택 button의 `aria-pressed=true`. |
| ORG-TC-E040 | Admin 상위 tab에서 제거된 `조직 설정`과 기존 deep link를 안전하게 처리해야 한다. | tab bar에 `조직 설정`이 남거나 `tab=organization` 접근 시 빈 화면 또는 제거된 panel이 표시된다. | `조직 설정` tab은 표시하지 않고 `tab=organization-structure&view=members`로 정규화해 조직 구성 화면을 표시. |
| ORG-TC-E041 | Sidebar 프로필 아이콘은 조직 초대 또는 권한 있는 열린 Security Alert가 있으면 알림 점을 표시하고 그 상태를 접근 가능하게 전달해야 한다. | 알림 source가 있는데 점이 없거나, 두 source가 비었는데 점이 남거나, 일반 member가 Security Alert source만으로 점을 보거나, 일반 `span`의 금지된 `aria-label`에 접근성 이름을 의존한다. | 프로필 우상단에 `aria-hidden` 빨간 점을 표시하고 프로필 button 이름에 `sr-only` 텍스트 `확인할 알림 있음`을 포함, source 0개면 둘 다 숨김. |
| ORG-TC-E042 | 같은 조직의 Security Alert summary background refresh는 마지막 성공 상태를 유지해야 한다. | 재조회 시작 또는 일시적 실패만으로 기존 알림 점이 사라진다. | 조회 중과 non-403 실패에는 기존 summary 유지, 성공 시 교체, 조직 전환 또는 403에서 제거. |
| ORG-TC-E043 | 펼친 Sidebar는 dashboard 본문 공간을 과도하게 차지하지 않아야 한다. | 펼친 상태의 너비가 `232px`가 아니거나 메뉴 문구가 잘린다. | 펼침 `232px`, 접힘 `80px` 유지, 모든 navigation 문구 표시. |
| ORG-TC-E044 | 권한 부여 modal은 복수 resource와 복수 active grantee를 checkbox로 선택해 bulk grant로 제출해야 한다. | radio로 하나만 선택하거나 pair별 HTTP 요청을 보내 partial success가 가능하다. | `resource_ids`, `grantee_ids`를 `POST /permissions/bulk-grants`로 한 번 전송하고 50 pair 초과를 차단. |
| ORG-TC-E045 | ActorAccessDrawer는 한 단계 큰 typography에서도 멤버십과 권한 정보를 읽기 쉽게 표시해야 한다. | 기존 text 계층이 유지되거나 최대 폭이 `2xl`이라 4열 지표·actor 정보가 불필요하게 줄바꿈된다. | `text-xs/sm/base/lg`를 한 단계씩 확대하고 최대 폭 `4xl` 유지. |

## Permission Tests

| ID | 검증 조건 | 최소 실패 조건 | 기대 결과 |
| --- | --- | --- | --- |
| ORG-TC-P001 | organization scope는 active membership과 active organization/user를 기준으로 해야 한다. | invited/suspended/removed membership, inactive organization, deactivated user 중 하나가 scope access를 허용한다. | access false. |
| ORG-TC-P002 | legacy created_by/managed_by fallback은 membership row가 없을 때만 manager로 동작해야 한다. | membership row가 있는데 fallback이 우선하거나, row가 없는데 legacy owner가 거부된다. | 정책에 맞게 허용/거부. |
| ORG-TC-P003 | resource organization mismatch는 creator fallback을 사용하지 않아야 한다. | 다른 organization header로 workflow owner fallback이 허용된다. | 404 또는 permission false. |
| ORG-TC-P004 | organization manager override는 resource permission helper의 공통 scope 경계에서 적용되어야 한다. | manager가 scope 안 resource의 organization-level manage/use 판정에서 거부된다. | manager auth state. |
| ORG-TC-P005 | resource별 추가 gate는 organization manager override만으로 자동 우회되지 않아야 한다. | organization manager라는 이유만으로 resource feature가 소유한 추가 authorization gate가 생략된다. | 해당 feature gate에서 별도 판단. |
| ORG-TC-P006 | workflow permission source 목록은 team과 user direct source를 구분해야 한다. | source type 또는 grantee id/name이 누락된다. | source list schema validation 통과. |
| ORG-TC-P007 | permission denied는 scope 안 denial에만 기록되고 scope 밖 resource는 숨겨야 한다. | scope 안 denial audit이 없거나 scope 밖 접근에 403/permission.denied audit이 발생한다. | audit recorded 또는 `404 resource.not_found`. |
| ORG-TC-P008 | auth failure는 organization permission check보다 먼저 닫혀야 한다. | token 없음인데 organization query나 body validation이 먼저 실행된다. | `auth.required`. |
| ORG-TC-P009 | Production workflow/Knowledge Base/LLM credential permission API는 target/team/user ORM model을 중앙 registry에서 해석해야 한다. | Endpoint가 registry와 별도 model import/switch를 사용하거나 unknown resource/grantee가 다른 permission table로 fallback한다. | Registry route와 API model identity 일치, unknown 값 fail-closed. |
| ORG-TC-P010 | Runtime authorization result는 principal-neutral revision contract를 반환해야 한다. | principal kind, authorization decision/resource/policy revision 또는 evaluated_at이 빠진다. | Missing field는 unknown/fail-closed. |
| ORG-TC-P011 | Relevant membership/permission mutation은 decision revision을 변경해야 한다. | User deactivation, membership suspend/remove/role change, team membership 또는 direct/team permission change 뒤 old revision이 그대로 allow된다. | New revision, stale consumer lease 거부. |
| ORG-TC-P012 | Anonymous public audience는 synthetic subject를 만들지 않아야 한다. | App owner/user id 또는 subject revision을 public principal에 주입한다. | `anonymous_public_audience`, subject 없음. |
| ORG-TC-P013 | Capability/credential/billing principal은 membership을 부여하지 않아야 한다. | Conversation grant나 credential owner만으로 internal membership/access가 허용된다. | Active current-user membership과 별도 permission 요구. |
