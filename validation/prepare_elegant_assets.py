"""Root-authored prompt package only. Does not generate, allocate attempts or accept art."""
from pathlib import Path
import ast, json, sys
repo=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repo/'novel_to_comic_skills/scripts'))
import comic_pipeline as cp
root=repo/'validation/灯塔来信'; project=cp.project_load(root)
cp.assert_script_lock(project,root)
style=('VERY ELEGANT CLEAN narrative manga: crisp fine dark-gray contour lines, lighter inner lines, '
       'flat clear ivory/sea-blue-gray colors, natural luminous skin, forest-green and terracotta accents, '
       'restrained two-tone cel shadows. Smooth surfaces, generous organized negative space, '
       'precise refined adult faces and graceful figures. NO paper grain, fabric noise, crosshatching, '
       'dense hair scratches, mottled skin, sepia wash, oily shine, brown haze, clutter or photoreal texture. '
       'Night retains clear ocean blue and small warm light areas, not blanket yellow tint. '
       'Identity and gender distinctions must remain; clean rendering must not simplify all faces into a common template. ')
out=root/'prompts/elegant';out.mkdir(parents=True,exist_ok=True)
jobs=[]
boards={
 'zhiwei-base': ('comparison-elegant','zhiwei-base'),
 'luyan-base': ('comparison-elegant','luyan-base'),
 'women-base': ('comparison-elegant','women-base'),
 'men-base': ('comparison-elegant','men-base'),
 'women-work': ('comparison-elegant','women-work'),
 'luyan-work': ('comparison-elegant','luyan-work')}
subjects={
 'zhiwei-base':['zhiwei'],'luyan-base':['luyan'],'women-base':['tangning','zhou'],
 'men-base':['xuchuan','zhao'],'women-work':['zhiwei','tangning'],'luyan-work':['luyan']}
chars={c['id']:c for c in project['script']['characters']}
for stem,(clean,old) in boards.items():
    version='work' if stem.endswith('work') else 'base'
    records=[{key:chars[cid][key] for key in ('id','name','visual','appearance','identity_card','appearance_versions')}
             for cid in subjects[stem]]
    prompt=('Use case: illustration-story. One complete character reference design board. '+style+
            'Image 1 is the NEW CLEAN style and face comparison; Image 2 is OLD identity/angle reference ONLY. '
            'Ignore Image 2 textures and paper background. Preserve its individual facial structures and anatomy. '
            'For EACH specified adult show frontal, three-quarter, exact side-profile face, complete full body '
            'with feet and common calm/focused/relieved expression portraits. Clean white background with breathing space. '
            'For two characters use exact LEFT and RIGHT equal separated halves in the listed order. '
            'No other characters. No labels, lettering, environment or montage of story scenes. '
            'Current version: '+version+'. Characters: '+json.dumps(records,ensure_ascii=False)+'. '
            'Luyan BASE shirt cuffs down to wrists. Luyan WORK gray shirt rolled sleeves. '
            'Zhiwei BASE ivory knit shawl on; WORK gray apron, no shawl. Tangning WORK gray apron. '
            'Apply only costumes relevant to the specified subjects and version.')
    path=out/(stem+'.txt');path.write_text(prompt,encoding='utf-8')
    jobs.append({'asset':stem+'-elegant','prompt_path':str(path.relative_to(root)),
                 'expected_reference_paths':['art/raw/'+clean+'.png','art/raw/'+old+'.png'],
                 'expected_output_path':'art/raw/'+stem+'-elegant.png','status':'not_generated',
                 'root_visual_review':'required; no pass inferred from text or file presence'})
room=('Use case: illustration-story. One empty environment reference, landscape 3:2. '+style+
      'Inside an old coastal lighthouse WORKSHOP, smooth light ivory stone walls shown as broad clean planes '
      'and only a few thin seam lines, simple light wooden center bench with absolutely EMPTY tabletop. '
      'LEFT tall sea-facing window, BACK neat sparse archive shelf, RIGHT open wooden entrance door, '
      'small plain electrical lever on back wall. Pale blue calm sea, gentle peach evening accent. '
      'A modest lamp mounting socket is separate and clearly empty, no installed lens. '
      'No people, lens, mounting ring, loose parts, carry boxes, iron boxes, toolboxes, lamps glowing, '
      'labels, writing, inserts, paper/fabric grain, rough wood textures or dense wall cracks. '
      'Elegant simple spatial geometry, calm restrained tones, abundant breathing space.')
(out/'workshop.txt').write_text(room,encoding='utf-8')
jobs.append({'asset':'workshop-elegant','prompt_path':'prompts/elegant/workshop.txt',
             'expected_reference_paths':[],'expected_output_path':'art/raw/workshop-elegant.png','status':'not_generated'})
prop=('Use case: illustration-story. Clean isolated prop reference on white, no characters. '+style+
      'ONE consistent original optical lens design: hand-sized transparent circular disc, shallow convex thickness '
      'rather than a hemisphere, one tiny hairline ONLY along the outer rim, no broad shattered cracks. '
      'Show same disc front and side, a matching plain rectangular light wooden carry box with padded recess, '
      'and matching simple bronze circular mounting socket. Socket view with missing fixing ring and another '
      'socket with ring fitted, in separated clean regions. Ring is one simple circular brass piece. '
      'Glass must remain same disc geometry in every view and inside case. No extra duplicate design variants, '
      'rough scratches, textual labels or glow. These are separate reference states, not one story moment.')
(out/'lens.txt').write_text(prop,encoding='utf-8')
jobs.append({'asset':'lens-elegant','prompt_path':'prompts/elegant/lens.txt','expected_reference_paths':[],
             'expected_output_path':'art/raw/lens-elegant.png','status':'not_generated'})
tree=ast.parse((repo/'validation/prepare_queue.py').read_text(encoding='utf-8'))
action=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='action' for t in n.targets))
action['p02']=('Exactly two: Zhiwei LEFT, empty hands, observes; Luyan RIGHT holds the OPEN wooden carry box '
                'with BOTH hands. Inside is ONE circular shallow optical glass disc with tiny OUTER RIM hairline. '
                'Do not assign box to Zhiwei. No loose rings or spare boxes on table. Luyan base sleeves DOWN.')
action['p10']=('Exactly Xuchuan LEFT, square-face and sturdy body, and Luyan RIGHT, slim angular face in SIDE PROFILE. '
                'Both gray rolled-sleeve shirts. Xuchuan uses screwdriver to secure ONE fixing ring on EMPTY bronze '
                'mounting socket; Luyan checks its spirit level. The ONE circular glass disc is NOT in socket yet; '
                'it stays in its open wooden carry box beside them. No duplicate disc or stray ring. Light OFF.')
action['p12']=('Exactly Luyan LEFT and Zhiwei RIGHT, both WORK outfits. Luyan closes wall electrical lever, subtly smiles. '
                'Zhiwei looks relieved towards newly illuminated optical lamp. Installed circular disc remains in '
                'its bronze socket aimed toward LEFT sea window; a single clear soft golden lighthouse beam travels '
                'OUT of that window over calm blue sea. Not a glowing desk lamp or multiple suns. Faces readable.')
for pid in action:
    action[pid]=action[pid].replace('rounded-square glass','circular shallow glass').replace('rounded-square','circular')
    action[pid]+=(' All tabletop props limited to this exact source moment: no spare iron box before p08; '
                  'no glass installed before p11; after p11 glass stays in installed socket, wooden case is EMPTY '
                  'until reply letter enters it in p15. No duplicate lens, gratuitous parts or second lighthouse '
                  'inside the window view. Keep background simple and smooth. This is state guidance, not extra action.')
panel_jobs=[]
for panel in project['script']['panels']:
    pid=panel['id']
    prompt=('Use case: illustration-story. Output ONE continuous landscape 3:2 story comic panel, no insets/grid. '+
            style+'Use only the NEW accepted elegant character boards for current costume versions, with regions '
            'specified at binding time. EMPTY workshop is layout only, prop sheet design only; never copy all '
            'its separate states into the scene. Action: '+action[pid]+'\nCast identity data: '+
            json.dumps({cid:{'identity_card':chars[cid]['identity_card'],'version':panel['appearance_versions'][cid]}
                        for cid in panel['cast']},ensure_ascii=False)+
            '\nNo printed dialogue, text, labels, captions, watermarks or panel borders. Letter/envelope surfaces '
            'blank. Complete important faces, anatomically correct hands, exact listed cast, no clones. '
            'Leads finely designed, women clearly feminine and men clearly masculine, age and build distinct.')
    path=out/(pid+'-draft.txt');path.write_text(prompt,encoding='utf-8')
    panel_jobs.append({'panel_id':pid,'prompt_path':str(path.relative_to(root)),
                       'state':'draft; actual reference IDs and regions must be added before begin-panel',
                       'attempt_allocated':False})
cp.atomic_json(root/'scripts/elegant-reference-jobs.json',jobs)
cp.atomic_json(root/'scripts/elegant-panel-drafts.json',panel_jobs)
print({'reference_jobs':len(jobs),'panel_prompt_drafts':len(panel_jobs),'attempts_allocated':0})
