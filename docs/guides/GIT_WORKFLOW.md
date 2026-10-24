# Git 워크플로우 가이드

GitHub Issue 기반의 개발 프로세스를 따르며, 모든 변경사항은 `dev` 브랜치를 중심으로 관리합니다.

---

## 브랜치 구조

| 브랜치         | 역할                             |
|-------------|--------------------------------|
| `main`      | 운영(배포) 환경, 항상 안정적인 상태 유지       |
| `dev`       | 개발 통합 브랜치, 모든 기능 개발의 기준점       |
| `feature/*` | 기능 개발, `dev`에서 분기              |
| `hotfix/*`  | 운영 중 발생한 긴급 버그 수정, `main`에서 분기 |

```
main ◄──────────────── hotfix/main-<n>
 ▲                            │
 │                            ▼ (dev에도 반영)
dev ◄─── feature/dev-<n>    dev
```

---

## Issue / Milestone 구조

### Milestone — 주요 작업 그룹

`dev` 하위의 주요 작업 단위(기능 영역)는 **Milestone**으로 생성합니다.

- **예시**: `공통 모듈 추가 (common)`, `회원 기능 추가 (account)`
- Milestone은 관련 Issue들을 묶어 진행률을 추적하는 데 사용합니다.

### Issue — 세부 작업

Milestone에 속하는 개별 작업은 **Issue**로 등록합니다.

- **Issue 제목**: 작업 내용을 간결하게 서술합니다.
- **예시**: `ExchangeModule 에 get_account() 메서드 추가`

> 하나의 issue 로 해결되는 작업이면 Milestone 을 등록 안해도 됩니다.

---

## 일반 개발 흐름 (feature)

1. **Milestone / Issue 생성**: 작업 그룹을 Milestone으로, 세부 작업을 Issue로 등록합니다.
2. **Issue Assign**: 작업할 Issue를 본인에게 Assign하면 브랜치가 자동 생성됩니다.
3. **작업 및 커밋**: [COMMIT.md](COMMIT.md) 규칙에 따라 커밋합니다.
4. **PR 생성**: `dev` 브랜치를 base로 PR을 생성합니다.
5. **코드 리뷰**: [CODE_REVIEW.md](CODE_REVIEW.md) 가이드에 따라 리뷰를 진행합니다.
6. **Rebase Merge**: 리뷰 완료 후 `dev`에 Rebase Merge합니다.

### 브랜치 생성

- **명명 규칙**: `<하위 브랜치명>/<상위 브랜치명>-<이슈번호>`
- **예시**: `feature/dev-12`

```bash
git checkout dev
git pull origin dev
git checkout -b feature/dev-12
```

### 작업 및 푸시

각 브랜치는 **커밋 1개** 단위로 작업합니다.

```bash
git push origin feature/dev-12
```

### Pull Request

PR은 브랜치의 **커밋 1개**를 그대로 반영하므로, 커밋의 header / body / footer를 그대로 작성합니다.

- **PR 제목**: 커밋의 header (예: `feat: 로그인 API 구현`)
- **body**: 커밋의 body — 변경 이유 및 내용
- **footer**: 커밋의 footer — `close`·`fix`·`resolve` 중 작업 성격에 맞는 키워드로 작성 (예: `close #12`), PR 머지 시 Issue 자동 종료

---

## dev → main 배포

`dev`의 개발이 완료되어 운영에 반영할 때는 **PR + Rebase Merge + Git Tag** 방식을 사용합니다.

### 배포 흐름

1. **PR 생성**: `dev` → `main` 으로 PR을 생성합니다.
2. **리뷰 및 승인**: 배포 범위와 변경사항을 최종 확인합니다.
3. **Rebase Merge**: GitHub UI에서 **"Rebase and merge"** 로 병합합니다.
4. **Git Tag**: 배포 시점을 태그로 기록합니다.

### Pull Request

- **PR 제목**: `release: v<MAJOR.MINOR.PATCH>` (예: `release: v1.2.0`)
- **body**: 이번 배포에 포함된 변경사항 요약

### Rebase Merge

Rebase and merge 방식으로 병합하면 `dev`의 커밋들이 `main` 위에 그대로 재적용되어 **선형 히스토리**를 유지합니다.

1. PR 페이지 하단 Merge 버튼 옆 드롭다운 클릭
2. **"Rebase and merge"** 선택
3. Merge 실행

### Git Tag 관리

태그는 `vMAJOR.MINOR.PATCH` 형식의 [시맨틱 버저닝](https://semver.org/lang/ko/)을 따릅니다.

| 구분    | 변경 시점                    |
|---------|------------------------------|
| `MAJOR` | 하위 호환이 불가능한 변경 |
| `MINOR` | 하위 호환 가능한 기능 추가 |
| `PATCH` | 하위 호환 가능한 버그 수정 |

```bash
# 태그 생성
git tag -a v1.2.0 -m "Release version 1.2.0"

# 태그 원격 push
git push origin v1.2.0

# 태그 목록 확인
git tag -l

# 특정 태그로 이동
git checkout v1.2.0
```

---

## Hotfix 흐름

운영(`main`)에서 긴급 버그가 발생한 경우 아래 절차를 따릅니다.

1. **Issue 생성**: 긴급 버그를 Issue로 등록합니다.
2. **Issue Assign**: 본인에게 Assign하면 `main`에서 브랜치가 자동 생성됩니다.
3. **작업 및 커밋**: 수정 사항을 커밋합니다.
4. **PR → `main`**: `main`을 base로 PR을 생성하고 Rebase Merge합니다.
5. **`dev`에 반영**: `main`의 변경사항을 `dev`에도 머지하여 동기화합니다.

### 브랜치 생성

- **명명 규칙**: `hotfix/main-<이슈번호>`
- **예시**: `hotfix/main-7`

```bash
git checkout main
git pull origin main
git checkout -b hotfix/main-7
```

### Pull Request

PR은 브랜치의 **커밋 1개**를 그대로 반영하므로, 커밋의 header / body / footer를 그대로 작성합니다.

- **PR 제목**: 커밋의 header (예: `fix: 결제 금액 계산 오류 수정`)
- **body**: 커밋의 body — 오류 원인 및 수정 내용
- **footer**: 커밋의 footer — `fix #<이슈번호>` 형식으로 작성 (예: `fix #7`), PR 머지 시 Issue 자동 종료

### dev 동기화

`main` 머지 완료 후 반드시 `dev`에도 반영합니다.

```bash
git checkout dev
git pull origin dev
git merge --no-ff main
git push origin dev
```

---

## Rebase Merge

모든 리뷰가 완료(Approve)되면 **Rebase and merge** 방식으로 합병합니다.

- 커밋 히스토리를 선형(Linear)으로 유지합니다.
- 불필요한 "Merge branch..." 커밋을 방지합니다.

**수행 방법 (GitHub UI 기준)**

1. PR 페이지 하단 Merge 버튼 옆 드롭다운 클릭
2. **"Rebase and merge"** 선택
3. Merge 실행

---

## 브랜치 삭제

합병이 완료된 브랜치는 원격과 로컬에서 모두 삭제합니다.

```bash
# 로컬 브랜치 삭제
git checkout dev
git branch -d feature/dev-12

# 원격 브랜치 삭제 (GitHub UI 버튼 사용 권장)
```
