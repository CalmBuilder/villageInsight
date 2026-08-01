from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class HeaderSemantics:
    leaf_label: str
    base_label: str
    normalized_base_label: str
    concept_key: str | None
    role: str | None
    role_evidence: str | None
    qualifier: str | None


ROLE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("household_head", ("户主", "户主本人")),
    ("applicant", ("申请人", "申报人")),
    ("beneficiary", ("受益人", "享受人", "补贴对象", "救助对象")),
    ("guardian", ("监护人", "监护照料人")),
    ("spouse", ("配偶", "妻子", "丈夫")),
    ("father", ("父亲", "父")),
    ("mother", ("母亲", "母")),
    ("child", ("子女", "儿子", "女儿")),
    ("contact", ("联系人",)),
    ("responsible_person", ("负责人", "责任人")),
    ("account_holder", ("开户人", "持卡人", "账户名")),
    ("payer", ("缴费人", "付款人")),
    ("member", ("家庭成员", "成员")),
    ("subject", ("本人",)),
    ("male_party", ("男方", "夫方")),
    ("female_party", ("女方", "妻方")),
)

CONCEPT_ALIASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "person.name",
        "姓名",
        (
            "姓名",
            "人员姓名",
            "个人姓名",
            "村民姓名",
            "农户姓名",
            "居民姓名",
            "成员姓名",
            "名字",
        ),
    ),
    (
        "person.identity_number",
        "身份证号",
        (
            "身份证号",
            "身份证号码",
            "公民身份号码",
            "居民身份证号码",
            "证件号码",
        ),
    ),
    (
        "person.phone",
        "联系电话",
        ("联系电话", "联系方式", "电话", "手机号", "手机号码", "电话号码"),
    ),
    (
        "person.gender",
        "性别",
        ("性别",),
    ),
    (
        "person.birth_date",
        "出生日期",
        ("出生日期", "出生年月", "出生时间"),
    ),
    (
        "person.address",
        "家庭地址",
        ("家庭地址", "家庭住址", "居住地址", "现住址"),
    ),
    (
        "finance.bank_account",
        "银行账号",
        ("银行账号", "银行账户", "银行卡号", "一卡通账号"),
    ),
    (
        "household.member_count",
        "家庭人口",
        ("家庭人口", "家庭人口数", "家庭成员数", "家庭总人口", "人数（人）", "人数(人)"),
    ),
    (
        "household.relationship_to_head",
        "与户主关系",
        (
            "与户主关系",
            "与户主关系（与派出所人口比对）",
            "与户主关系(与派出所人口比对)",
        ),
    ),
    (
        "governance.issue_location",
        "问题属地",
        ("问题属地", "事发村组"),
    ),
    (
        "governance.dispute_type",
        "矛盾纠纷类别",
        ("矛盾纠纷类别", "纠纷类别"),
    ),
)

AMBIGUOUS_BASE_LABELS = frozenset(
    {
        "名称",
        "编号",
        "编码",
        "类型",
        "类别",
        "状态",
        "日期",
        "时间",
        "金额",
        "数量",
        "面积",
        "地址",
        "备注",
        "是否",
    }
)

_UNIT_SUFFIX = re.compile(r"[（(][^（）()]{1,20}(?:元|亩|人|户|个|年|月|日|%|％)[）)]$")
_TITLE_MARKERS = ("明细", "台账", "清册", "汇总", "统计", "登记表", "信息表", "花名册")
_FIELD_LABEL_MARKERS = (
    "姓名",
    "名称",
    "编号",
    "编码",
    "号码",
    "身份证",
    "电话",
    "联系",
    "地址",
    "住址",
    "性别",
    "年龄",
    "民族",
    "日期",
    "时间",
    "金额",
    "数量",
    "面积",
    "类型",
    "类别",
    "状态",
    "备注",
    "关系",
    "户主",
    "家庭",
    "人口",
    "账号",
    "账户",
    "银行",
    "单位",
    "人员",
    "序号",
    "学历",
    "文化程度",
    "是否",
    "原因",
    "情况",
    "归属",
    "所在",
    "所属",
)
_SHORT_FIELD_LABELS = frozenset(
    {
        "乡",
        "镇",
        "村",
        "组",
        "户",
        "社区",
        "乡镇",
        "村组",
        "组别",
        "行政村",
        "自然村",
        "乡镇街道",
        "街道乡镇",
        "镇、街道",
        "村、社区",
        "村或社区",
    }
)
_OBSERVED_VALUE_SUFFIXES = (
    "有限公司",
    "合作社",
    "支行",
    "酒店",
    "服务中心",
    "卫生院",
    "环卫站",
    "村民委员会",
    "居民委员会",
)
_TITLE_VALUE_MARKERS = ("台账", "清册", "名册", "花名册", "汇总表", "统计表", "登记表")


def normalized_semantic_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def looks_like_observed_value_header(header_path: list[str]) -> bool:
    """Identify a data value that was accidentally selected as a header.

    This is a deterministic structure-quality rule only. Matching ignores the
    semantic projection for such a column while immutable raw cells remain
    available as source evidence.
    """
    if not header_path:
        return True
    leaf = " ".join(str(header_path[-1]).split()).strip()
    normalized = normalized_semantic_label(leaf)
    if not normalized:
        return True
    if leaf in _SHORT_FIELD_LABELS or any(marker in leaf for marker in _FIELD_LABEL_MARKERS):
        return False
    if re.fullmatch(r"(?:1[0-2]|[1-9])月", leaf):
        return False
    if re.fullmatch(r"[\d\s./:：年月日\-—至]+", leaf) and any(
        character.isdigit() for character in leaf
    ):
        return True
    if re.fullmatch(r"\d{6,}[0-9xX]?", normalized):
        return True
    if leaf in {"男", "女", "是", "否", "无", "有", "至今", "本人"}:
        return True
    if any(leaf.endswith(suffix) for suffix in _OBSERVED_VALUE_SUFFIXES):
        return True
    if any(marker in leaf for marker in _TITLE_VALUE_MARKERS) and len(normalized) >= 8:
        return True
    if re.search(r"\d{6,}", leaf):
        return True
    if "/" in leaf and any(character.isdigit() for character in leaf):
        return True
    if len(normalized) > 2 and leaf.endswith(("村", "社区", "街道", "乡", "镇")):
        return True
    return False


def semantic_header_path(header_path: list[str]) -> list[str]:
    """Remove observed values accidentally captured above a semantic leaf."""
    cleaned = [" ".join(str(part).split()).strip() for part in header_path]
    cleaned = [part for part in cleaned if part]
    if not cleaned or looks_like_observed_value_header([cleaned[-1]]):
        return []
    return [part for part in cleaned if not looks_like_observed_value_header([part])]


def _clean_label(value: str) -> str:
    compact = " ".join(unicodedata.normalize("NFKC", value).split()).strip()
    return _UNIT_SUFFIX.sub("", compact).strip()


def _role_from_text(value: str) -> tuple[str | None, str | None]:
    normalized = normalized_semantic_label(value)
    matches = [
        (len(normalized_semantic_label(alias)), role, alias)
        for role, aliases in ROLE_ALIASES
        for alias in aliases
        if normalized_semantic_label(alias) in normalized
    ]
    if not matches:
        return None, None
    _, role, alias = max(matches)
    return role, alias


def _remove_role_alias(value: str, alias: str | None) -> str:
    if not alias:
        return value
    stripped = value.replace(alias, "", 1).strip(" -_/·:：")
    return stripped or value


def _concept(value: str) -> tuple[str | None, str]:
    normalized = normalized_semantic_label(value)
    for concept_key, canonical_label, aliases in CONCEPT_ALIASES:
        if normalized in {normalized_semantic_label(alias) for alias in aliases}:
            return concept_key, canonical_label
    return None, value


def _qualifier(parent_path: list[str], base_label: str) -> str | None:
    if base_label not in AMBIGUOUS_BASE_LABELS:
        return None
    for raw_parent in reversed(parent_path):
        parent = _clean_label(raw_parent)
        normalized = normalized_semantic_label(parent)
        if not normalized or any(character.isdigit() for character in normalized):
            continue
        if len(normalized) > 16 or any(marker in parent for marker in _TITLE_MARKERS):
            continue
        return parent
    return None


def analyze_header_path(header_path: list[str]) -> HeaderSemantics:
    cleaned_path = [_clean_label(part) for part in header_path if _clean_label(part)]
    leaf = cleaned_path[-1] if cleaned_path else ""
    leaf_role, leaf_role_alias = _role_from_text(leaf)
    without_leaf_role = _remove_role_alias(leaf, leaf_role_alias)
    leaf_concept_key, leaf_base_label = _concept(without_leaf_role)
    if leaf_role in {"household_head", "mother", "father"} and (
        normalized_semantic_label(without_leaf_role) in {"实际", "未成年人"}
    ):
        leaf_concept_key, leaf_base_label = "person.name", "姓名"
    if leaf_role in {"guardian", "contact"} and leaf.endswith(("监护照料人", "联系人")):
        leaf_concept_key, leaf_base_label = "person.name", "姓名"
    if normalized_semantic_label(leaf) == "账户姓名":
        leaf_concept_key, leaf_base_label = "person.name", "姓名"
        leaf_role, leaf_role_alias = "account_holder", "账户"
    if normalized_semantic_label(leaf).endswith("账户身份证号"):
        leaf_concept_key, leaf_base_label = "person.identity_number", "身份证号"
        leaf_role, leaf_role_alias = "account_holder", "账户"
    role = leaf_role if leaf_concept_key else None
    role_alias = leaf_role_alias if role else None
    role_evidence = leaf if role else None
    if role is None and leaf_concept_key is not None:
        for parent in reversed(cleaned_path[:-1]):
            if ("联系人" in parent and "联系电话" in parent) or parent.count("：") + parent.count(
                ":"
            ) > 1:
                continue
            role, role_alias = _role_from_text(parent)
            if role:
                role_evidence = parent
                break
    if role_evidence == leaf:
        concept_key, base_label = leaf_concept_key, leaf_base_label
    else:
        concept_key, base_label = _concept(leaf)
    return HeaderSemantics(
        leaf_label=leaf,
        base_label=base_label,
        normalized_base_label=normalized_semantic_label(base_label),
        concept_key=concept_key,
        role=role,
        role_evidence=role_evidence,
        qualifier=_qualifier(cleaned_path[:-1], base_label),
    )


def semantic_identity(
    *,
    header_path: list[str],
    domain: str,
    observed_data_type: str | None = None,
    unit_dimension: str | None = None,
) -> dict[str, str | None]:
    semantics = analyze_header_path(header_path)
    if semantics.concept_key:
        return {
            "concept": semantics.concept_key,
            "domain": None,
            "qualifier": None,
            "data_type": observed_data_type,
            "unit": unit_dimension,
        }
    return {
        "concept": semantics.normalized_base_label,
        "domain": domain if semantics.base_label in AMBIGUOUS_BASE_LABELS else None,
        "qualifier": (
            normalized_semantic_label(semantics.qualifier) if semantics.qualifier else None
        ),
        "data_type": observed_data_type,
        "unit": unit_dimension,
    }


def equivalent_semantic_labels(value: str) -> set[str]:
    semantics = analyze_header_path([value])
    labels = {
        normalized_semantic_label(value),
        semantics.normalized_base_label,
    }
    if semantics.concept_key:
        for concept_key, canonical_label, aliases in CONCEPT_ALIASES:
            if concept_key != semantics.concept_key:
                continue
            labels.add(normalized_semantic_label(canonical_label))
            labels.update(normalized_semantic_label(alias) for alias in aliases)
            break
    return {label for label in labels if label}


def semantic_candidate_is_compatible(
    *,
    header_path: list[str],
    candidate_labels: list[str],
    reasons: list[str] | tuple[str, ...] = (),
) -> bool:
    """Require semantic identity, not mere substring overlap, before field reuse."""
    if "full_header_path" in reasons:
        return True
    source = analyze_header_path(header_path)
    candidates = [analyze_header_path([label]) for label in candidate_labels if label.strip()]
    if source.concept_key is not None:
        return any(candidate.concept_key == source.concept_key for candidate in candidates)
    return any(
        candidate.normalized_base_label == source.normalized_base_label for candidate in candidates
    )


def header_paths_equivalent(
    expected: list[str] | tuple[str, ...],
    actual: list[str] | tuple[str, ...],
) -> bool:
    expected_parts = [" ".join(part.split()) for part in expected if part.strip()]
    actual_parts = [" ".join(part.split()) for part in actual if part.strip()]
    if expected_parts == actual_parts:
        return True
    expected_without_title = _without_document_title(expected_parts)
    actual_without_title = _without_document_title(actual_parts)
    if (
        expected_without_title
        and expected_without_title == actual_without_title
        and (expected_without_title != expected_parts or actual_without_title != actual_parts)
    ):
        return True
    shorter, longer = (
        (expected_parts, actual_parts)
        if len(expected_parts) < len(actual_parts)
        else (actual_parts, expected_parts)
    )
    if not shorter or longer[-len(shorter) :] != shorter:
        return False
    title_prefix = " ".join(longer[: -len(shorter)])
    return any(marker in title_prefix for marker in ("表", "名册", "台账", "登记", "汇总", "清册"))


def _without_document_title(parts: list[str]) -> list[str]:
    if len(parts) < 2:
        return parts
    first = parts[0]
    if any(marker in first for marker in ("表", "名册", "台账", "登记", "汇总", "清册", "统计")):
        return parts[1:]
    return parts


def supported_role_codes() -> tuple[str, ...]:
    return tuple(role for role, _ in ROLE_ALIASES)


def normalize_role_code(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalized_semantic_label(value)
    supported = {normalized_semantic_label(role): role for role, _ in ROLE_ALIASES}
    for role, aliases in ROLE_ALIASES:
        for alias in aliases:
            supported[normalized_semantic_label(alias)] = role
    supported.update(
        {
            "head": "household_head",
            "householdhead": "household_head",
            "primarycontact": "contact",
            "duplicate": "duplicate",
            "registrycomparison": "registry_comparison",
        }
    )
    if normalized in supported:
        return supported[normalized]
    duplicate = re.fullmatch(r"duplicate(\d+)", normalized)
    if duplicate:
        return f"duplicate_{duplicate.group(1)}"
    date_role = re.fullmatch(r"(?:date|asof)(\d{4})(\d{2})(\d{2})", normalized)
    if date_role:
        return f"date_{date_role.group(1)}_{date_role.group(2)}_{date_role.group(3)}"
    return None
