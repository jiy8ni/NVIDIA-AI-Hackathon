"""Export the verified workspace, never run a second synthesis for PDF text."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from .providers import ROOT, safe_url, FixtureProvider
from .models import OfflineModel
from .engine import Orchestrator
from .contracts import RunRequest


def export(workspace, destination, font_path=None):
    if workspace['status'] == 'generating':
        raise ValueError('Wait for generation to complete before exporting')
    font = Path(font_path or os.getenv('HANDOFF_PDF_FONT', 'C:/Windows/Fonts/malgun.ttf'))
    if not font.is_file():
        raise ValueError('Set HANDOFF_PDF_FONT to a Korean-capable TrueType font')
    pdfmetrics.registerFont(TTFont('HandoffKorean', str(font)))
    regular = ParagraphStyle('body', fontName='HandoffKorean', fontSize=9.4, leading=15, textColor=colors.HexColor('#223247'), spaceAfter=8, wordWrap='CJK')
    title = ParagraphStyle('title', parent=regular, fontSize=23, leading=32, textColor=colors.HexColor('#12364a'), spaceAfter=18)
    heading = ParagraphStyle('heading', parent=regular, fontSize=12, leading=18, spaceBefore=12, spaceAfter=8, keepWithNext=True)
    small = ParagraphStyle('small', parent=regular, fontSize=8, leading=12, textColor=colors.HexColor('#566778'))
    labels = {'FACT': '원문에 명시', 'INFERENCE': '근거 기반 추론', 'SUGGESTION': '제안', 'UNKNOWN': '미확정'}
    story = []
    source_numbers = {s['id']: i+1 for i, s in enumerate(workspace['sources'])}
    def text(value, style=regular):
        story.append(Paragraph(escape(str(value)).replace('\n', '<br/>'), style))
    def field(label, value):
        text(f"{label}: [{labels.get(value.get('claimType'), '미확정')}] {value.get('value') or '확인 필요'}")
    for number, section in enumerate(workspace['sections']):
        if number:
            story.append(PageBreak())
        text('HANDOFFOS / VERIFIED WORKSPACE', small)
        text(section['number'] + '. ' + section['title'], title)
        if number == 0:
            text('역할별 온보딩 · 인수인계', heading)
            text(f"워크스페이스: {workspace['id']} | 상태: {workspace['status']}", small)
            text('원문에 기록된 내용과 업무 승인 여부는 다릅니다. 외부 서비스에 메시지를 보내거나 문서를 수정하지 않습니다.', small)
        for block in section['blocks']:
            p = block['payload']; kind = block['type']
            text(block['title'], heading)
            if kind == 'job':
                field('목적', p['objective']); field('담당자', p.get('ownerName', p['ownerId']))
                field('기한', p['dueAt'] if p['dueAt']['value'] else {'value': p['dueText'], 'claimType': 'FACT' if p['dueText'] else 'UNKNOWN'})
                field('다음 행동', p['nextAction'])
                for done in p['definitionOfDone']: field('완료 조건', done)
                if not p['definitionOfDone']: text('완료 조건: [미확정] 확인 필요')
                for step in p['steps']: field(f"절차 {step['order']}", step['action'])
                if not p['steps']: text('절차: 원문에 명시되지 않음')
                text('선행 업무: ' + (', '.join(d['title'] for d in p.get('dependencies', [])) or '명시된 근거 없음'), small)
                text('차단 요소: ' + (', '.join(b['text'] for b in p.get('blockers', [])) or '명시된 근거 없음'), small)
                for question in p.get('unresolvedQuestions', []): text('[미확정] ' + question, small)
                text('신뢰도: 원문 근거 일치 검증 / 승인 및 배정 확인은 별도', small)
            elif kind == 'person-card':
                person = next((x for x in workspace['people'] if x['id'] == p.get('personId')), None)
                if person:
                    text('[원문에 명시] ' + person['name'] + ' / ' + person['role'])
                    text('요청 역할에 대한 관계: ' + {'manager': '직속 관리자', 'teammate': '팀원', 'collaborator': '협업자', 'subject-matter-expert': '분야 전문가'}[person['relationship']])
            elif kind == 'timeline':
                timeline = next((x for x in workspace['timelines'] if x['projectId'] == p.get('projectId')), None)
                if timeline:
                    text(block.get('body') or '', small)
                    for milestone in timeline['milestones']:
                        text('[원문에 명시] ' + milestone['date'] + ' / ' + milestone['title'] + ' / ' + {'done': '완료', 'next': '다음', 'future': '예정', 'blocked': '차단'}[milestone['status']])
            elif kind == 'comparison':
                text('[근거 기반 추론] 상충 가능성. 최신 자료를 자동 승인하지 않았습니다.')
                for row in p['rows']:
                    text(row['label'], small); text(row['claim']['text'])
                text(block.get('body') or '')
            elif kind == 'unknown-card':
                text('[미확정] ' + p['question']); text(p['whyItMatters'])
                text('확인 담당자: ' + (p['suggestedOwnerId'] or '미확정'))
                for c in p.get('contactCandidates', []): text('질문 후보: ' + c['name'] + ' - ' + c['basis'])
                text('질문 초안 (전송하지 않음): ' + p['draftQuestion'])
            elif kind == 'checklist':
                text('[제안] 첫 주 읽을 자료 순서')
                for i, item in enumerate(x for x in workspace['progress']['checklist'] if x['id'] in p['itemIds']):
                    text(f"{i+1}. [{'완료' if item['completed'] else '진행 전'}] {item['label']}")
            else:
                if kind == 'paragraph': text('[원문에 명시]', small)
                text(block.get('body') or '')
            linked = [source_numbers[s] for s in block.get('sourceIds', []) if s in source_numbers]
            if linked: text('근거: ' + ', '.join(f'[{i}]' for i in linked), small)
    story.append(PageBreak())
    text('출처와 확인 방법', title)
    text('PDF는 이미 검증된 화면용 콘텐츠를 내보냅니다. 이후 원본이 바뀔 수 있으며, 아래 링크에서 현재 내용을 재확인하세요. example.com은 가상 데모 자료 링크입니다.')
    for s in workspace['sources']:
        text(f"[{source_numbers[s['id']]}] {s['title']}", heading)
        text(s['provider'] + ' / ' + s['id'], small)
        if safe_url(s['url']):
            url = escape(s['url'], {'"': '&quot;'})
            story.append(Paragraph(f'<link href="{url}" color="#165b8c">{url}</link>', small))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    def footer(canvas, doc):
        canvas.setFont('HandoffKorean', 8)
        canvas.setFillColor(colors.HexColor('#566778'))
        canvas.drawString(42, 25, 'HandoffOS | 근거 기반 온보딩 | 읽기 전용')
        canvas.drawRightString(A4[0]-42, 25, str(doc.page))
    SimpleDocTemplate(str(destination), pagesize=A4, leftMargin=42, rightMargin=42, topMargin=38, bottomMargin=45, title='HandoffOS 온보딩', author='HandoffOS').build(story, onFirstPage=footer, onLaterPages=footer)
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default=str(ROOT / '.runtime/state.json'))
    parser.add_argument('--user-id', default='kim-juhyung')
    parser.add_argument('--output', default=str(ROOT / 'output/pdf/handoffos-onboarding.pdf'))
    parser.add_argument('--demo', action='store_true', help='Generate an explicitly offline fixture example')
    args = parser.parse_args()
    if args.demo:
        request = RunRequest(mode='generate', scope={'userId': args.user_id, 'teamId': 'atlas', 'sources': ['slack', 'notion', 'drive']}, role='운영 담당자')
        generated = asyncio.run(Orchestrator(provider=FixtureProvider(), model=OfflineModel()).run(request))['result']
        items = generated.pop('checklist')
        workspace = {'id': 'offline-example', 'status': 'ready', **generated, 'progress': {'completed': 0, 'total': len(items), 'percent': 0, 'currentSectionId': 'company', 'checklist': items}}
    else:
        saved = json.loads(Path(args.state).read_text(encoding='utf-8'))
        user = next(user for user in saved['users'].values() if user['userId'] == args.user_id)
        if user.get('retrievalMode') != 'fixture':
            raise ValueError('CLI PDF export is fixture-only until live ACL revalidation is integrated; regenerate a fixture workspace or use --demo')
        workspace = user['workspace']
    print(export(workspace, args.output))


if __name__ == '__main__':
    main()
