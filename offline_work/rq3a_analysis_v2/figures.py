"""Small publication-style SVGs with explicit incomplete-preview labels."""
from reportlab.graphics import renderSVG
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib.colors import Color, black, white

LABELS = {'proposed':'Proposed', 'fault_matching':'Fault matching', 'whole_environment':'Whole environment'}
METHODS = tuple(LABELS)
COLORS = (Color(.78,.78,.78), Color(.32,.32,.32), white)

def heading(title, result):
    d = Drawing(720, 300)
    d.add(Rect(0, 0, 720, 300, fillColor=white, strokeColor=white))
    d.add(String(10, 278, title, fontName='Times-Bold', fontSize=16, fillColor=black))
    status = ('Complete collection' if result['status'] == 'FINAL_DESCRIPTIVE_ANALYSIS' else 'INCOMPLETE COLLECTION - DEVELOPMENT PREVIEW')
    d.add(String(10, 255, status, fontName='Times-Bold', fontSize=12, fillColor=black))
    d.add(String(10, 236, f"Accepted snapshot: {result['completed_at_snapshot']}/240 responses", fontName='Times-Roman', fontSize=11))
    return d

def create_figures(result, output):
    d = heading('Initial decisions by response method', result)
    decisions = ('Direct reuse','Revalidate','Stop reuse')
    maximum = max(1, max(r['eligible_accepted_incidents'] for r in result['per_method'].values()))
    x0, width = 170, 450
    for tick in range(5):
        x = x0 + width * tick/4
        d.add(Line(x, 62, x, 197, strokeColor=Color(.88,.88,.88)))
        d.add(String(x, 46, f'{maximum*tick/4:g}', textAnchor='middle', fontName='Times-Roman', fontSize=10))
    for i, method in enumerate(METHODS):
        y = 175 - i*44
        d.add(String(x0-12, y+9, LABELS[method], textAnchor='end', fontName='Times-Roman', fontSize=12))
        offset = 0
        for j, decision in enumerate(decisions):
            n = result['per_method'][method]['initial_decisions'][decision]
            w = width * n/maximum
            if n:
                d.add(Rect(x0+offset,y,w,25,fillColor=COLORS[j],strokeColor=black,strokeWidth=.5))
                d.add(String(x0+offset+w/2,y+8,str(n),textAnchor='middle',fontName='Times-Roman',fontSize=11,fillColor=white if j==1 else black))
            offset += w
        d.add(String(x0+offset+8,y+8,f"n={result['per_method'][method]['eligible_accepted_incidents']}",fontName='Times-Roman',fontSize=10))
    for i, decision in enumerate(decisions):
        x = 170+i*158
        d.add(Rect(x,18,10,10,fillColor=COLORS[i],strokeColor=black,strokeWidth=.5))
        d.add(String(x+16,18,decision,fontName='Times-Roman',fontSize=11))
    renderSVG.drawToFile(d, str(output/'initial-decisions.svg'))
    d = heading('Invalid initial Direct reuse executions', result)
    for tick in range(5):
        x = x0 + width*tick/4
        d.add(Line(x,62,x,197,strokeColor=Color(.88,.88,.88)))
        d.add(String(x,46,f'{tick*25}%',textAnchor='middle',fontName='Times-Roman',fontSize=10))
    for i, method in enumerate(METHODS):
        y = 175-i*44
        values = result['per_method'][method]
        numerator = values['initial_direct_outcomes']['Confirmed failure']
        denominator = values['determinate_initial_direct_denominator']
        ratio = values['conditional_invalid_direct_rate']
        d.add(String(x0-12,y+9,LABELS[method],textAnchor='end',fontName='Times-Roman',fontSize=12))
        if ratio is not None:
            w = width*ratio
            if w:d.add(Rect(x0,y,w,25,fillColor=COLORS[0],strokeColor=black,strokeWidth=.5))
            label = f'{numerator}/{denominator} ({100*ratio:.1f}%)'
        else:
            w=0;label='Not applicable: no determinate initial Direct reuse execution'
        d.add(String(x0+w+8,y+8,label,fontName='Times-Roman',fontSize=11))
    d.add(String(10,18,'Revalidation executions and uncertain outcomes are reported separately. Zero failures do not establish general reliability.',fontName='Times-Roman',fontSize=10))
    renderSVG.drawToFile(d,str(output/'conditional-invalid-direct.svg'))
