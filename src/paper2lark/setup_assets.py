"""Packaged defaults for a new English or Chinese paper library."""

from importlib import resources
import json
import re

from .bindings import LABELS, TYPES
from .errors import Paper2LarkError


def _keyword_seed():
    try:
        seed = json.loads(resources.files('paper2lark').joinpath('keywords.en.json').read_text(encoding='utf-8'))
        labels = seed['keywords']
        if (seed['language'] != 'en' or type(seed['max_words_per_keyword']) is not int
                or seed['max_words_per_keyword'] != 3
                or type(seed['max_keywords_per_paper']) is not int or seed['max_keywords_per_paper'] != 8
                or not isinstance(labels, list) or not 1 <= len(labels) <= 10000
                or any(not isinstance(label, str) or not re.fullmatch(r'[A-Za-z]+(?:[- ][A-Za-z]+)*', label)
                       or len(label.split()) > 3 for label in labels)
                or len({label.casefold() for label in labels}) != len(labels)):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise Paper2LarkError('SETUP_ASSET_INVALID', 'The packaged English keyword seed is invalid.') from error
    return labels


_EN_TEMPLATE = """# Paper Reading Note

## Metadata and provenance
Record title, authors, year, venue, DOI or arXiv identifier, source URL, version, and reading date. Identify the exact paper files and supplements used; distinguish source facts from inference. Use English keywords of at most three words each, at most eight per paper.

## Question and contribution
State the research question, motivation, and claimed contribution. Identify whether this is a research paper or review; omit inapplicable sections explicitly.

## Research: methods and results
Explain the method, assumptions, datasets, baselines, experimental settings, metrics, and key results. Preserve units, denominators, uncertainty and comparisons; do not invent missing numbers.

## Review: scope and synthesis
Describe the search scope, inclusion criteria, taxonomy, areas of agreement and disagreement, evidence quality, and open questions. Distinguish a narrative review from a systematic review and report unavailable search details.

## Evidence and reproducibility
Anchor major claims to sections, pages, figures, tables or equations. Link code and data when supplied. Explain what was directly verified, what is inferred, and what remains unavailable.

## Limitations and critical assessment
Separate author-stated limitations from reader analysis. Assess validity, generalization, missing controls, alternative explanations, and unresolved questions without overstating the evidence.

## Personal notes — human-only
Leave this section for the human reader to fill manually. AI must preserve its contents and must not write personal opinions on the reader's behalf.
"""

_ZH_TEMPLATE = """# 论文阅读笔记

## 元信息与来源追溯（provenance）
记录标题、作者、年份、发表场所、DOI 或 arXiv 标识、原文链接、版本及阅读日期。注明使用的论文文件和补充材料，区分原文事实与推断。关键词使用英文，每个最多三个词，每篇最多八个。

## 问题与贡献
说明研究问题、动机和作者声称的贡献。判断属于研究论文（research）还是综述（review），明确标注不适用的部分。

## 研究论文（research）：方法与结果
解释方法、假设、数据集、基线、实验设置、指标和主要结果。保留单位、分母、不确定性及比较条件，不编造缺失数字。

## 综述（review）：范围与综合
说明检索范围、纳入标准、分类体系、共识与分歧、证据质量和开放问题。区分叙述性综述与系统综述，明确未提供的检索信息。

## 证据与可复现性（evidence）
为主要结论标注章节、页码、图、表或公式位置。提供原文给出的代码和数据链接。区分直接核实、推断及无法获得的信息。

## 局限与批判性分析（limitations）
区分作者自述局限与阅读分析。评估有效性、泛化性、缺失对照、替代解释及未解决的问题，不夸大证据。

## 个人笔记（human-only，人工填写）
本节留给读者手动填写。AI 必须保留已有内容，不得代写读者的个人观点。
"""


def library_assets(language):
    """Return fresh CLI field definitions, status labels, titles and Markdown."""
    if not isinstance(language, str) or language not in ('en', 'zh-CN'):
        raise Paper2LarkError('SETUP_LANGUAGE_UNSUPPORTED', 'Library language must be en or zh-CN.')
    english = language == 'en'
    statuses = dict(zip(('unread', 'reading', 'read'), ('Unread', 'Reading', 'Read') if english else ('待读', '在读', '已阅')))
    fields = {key: {'type': TYPES[key], 'name': labels[1 if english else 0]} for key, labels in LABELS.items()}
    for key in ('source_url', 'note_url'):
        fields[key]['style'] = {'type': 'url'}
    fields['keywords'].update(multiple=True, options=[{'name': label} for label in _keyword_seed()])
    fields['reading_status'].update(multiple=False, options=[{'name': label} for label in statuses.values()], default_value=[statuses['unread']])
    fields['priority'].update(multiple=False, options=[{'name': label} for label in (('High', 'Medium', 'Low') if english else ('高', '中', '低'))])
    fields['added_at']['style'] = {'format': 'yyyy-MM-dd HH:mm'}
    titles = dict(zip(('root', 'index', 'table', 'notes', 'template'),
                      ('Paper Library', 'Paper Index', 'Papers', 'Reading Notes', 'Reading Template') if english
                      else ('论文库', '论文索引', '论文', '阅读笔记', '阅读模板')))
    return {'fields': fields, 'statuses': statuses, 'titles': titles,
            'template': _EN_TEMPLATE if english else _ZH_TEMPLATE}
