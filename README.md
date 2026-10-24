# Starter Kit

GitHub Issue 기반 협업 워크플로우를 빠르게 적용할 수 있는 스타터킷입니다.

---

## 목차

1. [구성 요소](#구성-요소)
2. [시작하기](#시작하기)
3. [브랜치 구조](#브랜치-구조)
4. [개발 흐름](#개발-흐름)
5. [자동화](#자동화)
6. [가이드](#가이드)

---

## 구성 요소

```
.
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── config.yml          # 빈 이슈 생성 비활성화
│   │   ├── FEATURE.md          # 기능 추가 이슈 템플릿
│   │   ├── BUG.md              # 버그 수정 이슈 템플릿
│   │   ├── REFACTOR.md         # 리팩토링 이슈 템플릿
│   │   ├── SETUP.md            # 프로젝트 초기 세팅 이슈 템플릿
│   │   └── HOTFIX.md           # 긴급 버그 수정 이슈 템플릿
│   ├── workflows/
│   │   └── create-branch-on-issue.yml  # Issue Assign 시 브랜치 자동 생성
│   └── issue-branch.yml        # 브랜치 자동 생성 규칙
└── docs/
    └── guides/
        ├── COMMIT.md           # 커밋 메시지 작성 가이드
        ├── GIT_WORKFLOW.md     # Git 워크플로우 가이드
        └── CODE_REVIEW.md      # 코드 리뷰 가이드
```

---

## 시작하기

### 1. 레포지토리 생성

이 레포지토리를 템플릿으로 사용하거나 파일을 복사합니다.

### 2. `dev` 브랜치 생성

```bash
git checkout -b dev
git push origin dev
```

### 3. 기본 브랜치 설정

GitHub 레포지토리 설정에서 기본 브랜치를 `dev`로 변경합니다.

> `Settings` → `General` → `Default branch` → `dev`

---

## 브랜치 구조

| 브랜치 | 역할 |
|--------|------|
| `main` | 운영(배포) 환경, 항상 안정적인 상태 유지 |
| `dev` | 개발 통합 브랜치, 모든 기능 개발의 기준점 |
| `feature/dev-<n>` | 기능 개발, `dev`에서 분기 |
| `hotfix/main-<n>` | 운영 중 발생한 긴급 버그 수정, `main`에서 분기 |

```
main ◄──────────────── hotfix/main-<n>
 ▲                            │
 │                            ▼ (dev에도 반영)
dev ◄─── feature/dev-<n>    dev
```

---

## 개발 흐름

### 기능 개발 (feature)

1. **Issue 생성** — 작업을 Issue로 등록합니다.
2. **Issue Assign** — 본인에게 Assign하면 브랜치가 자동 생성됩니다.
3. **작업 및 커밋** — [커밋 가이드](docs/guides/COMMIT.md) 규칙에 따라 커밋합니다.
4. **PR 생성** — `dev`를 base로 PR을 생성합니다.
5. **코드 리뷰** — [코드 리뷰 가이드](docs/guides/CODE_REVIEW.md)에 따라 리뷰합니다.
6. **Rebase Merge** — 리뷰 완료 후 `dev`에 Rebase Merge합니다.

### 배포 (dev → main)

1. **PR 생성** — `dev` → `main` PR을 생성합니다. (제목: `release: v1.0.0`)
2. **리뷰 및 승인** — 배포 범위와 변경사항을 최종 확인합니다.
3. **Rebase Merge** — GitHub UI에서 **"Rebase and merge"** 로 병합합니다.
4. **Git Tag** — 머지 후 배포 버전을 태그로 기록합니다.

### 긴급 버그 수정 (hotfix)

1. `main`에서 `hotfix/main-<n>` 브랜치 생성
2. 수정 후 `main`으로 PR → Rebase Merge
3. `main` 변경사항을 `dev`에도 반영

---

## 자동화

### Issue Assign → 브랜치 자동 생성

Issue를 본인에게 Assign하면 `issue-branch.yml` 규칙에 따라 브랜치가 자동 생성됩니다.

| label | 생성 브랜치 |
|-------|------------|
| `enhancement`, `setup`, `bug`, `refactor` | `feature/dev-<이슈번호>` |
| `hotfix` | `hotfix/main-<이슈번호>` |

---

## 가이드

- [커밋 메시지 작성 가이드](docs/guides/COMMIT.md)
- [Git 워크플로우 가이드](docs/guides/GIT_WORKFLOW.md)
- [코드 리뷰 가이드](docs/guides/CODE_REVIEW.md)