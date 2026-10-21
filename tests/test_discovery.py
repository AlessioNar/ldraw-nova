import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from conftest import mpd, ref
from ldraw_tools.common import atomic_write
from ldraw_tools.discovery import DiscoveryIndex, confined, decode_description, record_id, resolve_engine, search, part_suggestions
from ldraw_tools.document import extract_section, selected_source
from ldraw_tools.resources import model_sections, search_models
from ldraw_tools.validation import validate_text


@pytest.fixture
def index(parts, tmp_path):
    root=tmp_path/'models';root.mkdir()
    atomic_write(root/'a.mpd',mpd(ref('cab.ldr'),'a.ldr')+mpd(ref()+ '\n'+ref('3003.dat',position='80 0 0'),'cab.ldr'))
    atomic_write(root/'b.mpd',mpd(ref('cab.ldr'),'b.ldr')+mpd(ref(position='100 0 0')+'\n'+ref('3003.dat',position='180 0 0'),'cab.ldr'))
    atomic_write(root/'empty.mpd',mpd('','empty.ldr'))
    atomic_write(root/'single.mpd','0 Descriptive tugboat hull\r\n0 Name: tugboat.ldr\r\n0 Author: Source author\r\n'+ref()+'\r\n')
    db=tmp_path/'source.sqlite'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE MODELS_DESCRIPTIONS(model TEXT PRIMARY KEY,description TEXT)')
        c.execute('CREATE TABLE SUBMODELS_DESCRIPTIONS(model TEXT,submodel TEXT,description TEXT,PRIMARY KEY(model,submodel))')
        c.execute('CREATE TABLE PARTS_DESCRIPTIONS(part TEXT PRIMARY KEY,description TEXT)')
        c.executemany('INSERT INTO MODELS_DESCRIPTIONS VALUES(?,?)',[('a.mpd','Truck'),('b.mpd','Truck'),('empty.mpd','Nothing'),('single.mpd','Name: tugboat.ldr')])
        c.executemany('INSERT INTO SUBMODELS_DESCRIPTIONS VALUES(?,?,?)',[('a.mpd','cab.ldr','Truck cab'),('b.mpd','cab.ldr','Truck cab')])
        c.executemany('INSERT INTO PARTS_DESCRIPTIONS VALUES(?,?)',[('3001.dat','Brick | rectangular'),('s/internal.dat','Brick internal')])
        for kind,prefix in [('MODELS',"model || '|'"),('SUBMODELS',"model || '|' || submodel || '|'"),('PARTS',"part || '|'")]:
            c.execute(f'CREATE VIEW {kind}_DESCRIPTIONS_JEV AS SELECT {prefix} || description AS full_description FROM {kind}_DESCRIPTIONS')
    instance=DiscoveryIndex(parts,root,database=db,cache=tmp_path/'cache')
    instance.ensure()
    return instance


def test_source_identity_does_not_depend_on_view_row_order(index):
    before=index.database.read_bytes()
    a=decode_description('submodels','a.mpd|cab.ldr|Changed description')
    b=decode_description('submodels','a.mpd|cab.ldr|Other prose')
    assert record_id(a)==record_id(b)
    assert index.get(record_id(a))['model']=='a.mpd'
    assert index.database.read_bytes()==before
    assert decode_description('parts','3001.dat|Brick | rectangular')['description']=='Brick | rectangular'
    with pytest.raises(ValueError):decode_description('submodels','missing|section')


@pytest.mark.parametrize('name',['../escape.mpd','/absolute.mpd','C:\\outside.mpd','..\\escape.mpd'])
def test_source_root_is_enforced(tmp_path,name):
    with pytest.raises(ValueError):confined(tmp_path,name)


def test_source_headers_correct_the_local_index_without_mutating_database(index):
    row=next(r for r in index.rows('models') if r['model']=='single.mpd')
    assert row['description']=='Descriptive tugboat hull'
    assert row['indexed_description']=='Name: tugboat.ldr'
    assert row['description_corrected']
    with sqlite3.connect(index.database) as c:
        assert c.execute('SELECT description FROM MODELS_DESCRIPTIONS WHERE model="single.mpd"').fetchone()[0]=='Name: tugboat.ldr'


def test_single_section_can_be_extracted_and_selected(index,parts):
    path=index.root/'single.mpd';before=path.read_bytes()
    rows=model_sections(path)['sections'];assert not rows[0]['has_file']
    copied,manifest=extract_section(path,rows[0]['name'],namespace='single')
    _,diagnostics=validate_text(copied,parts)
    assert not [d for d in diagnostics if d['severity']=='error']
    assert 'Source author' in copied and 'Descriptive tugboat hull' in copied
    assert manifest['source_sha256']==hashlib.sha256(before).hexdigest()
    assert path.read_bytes()==before
    _,line_map=selected_source(path,rows[0]['name'])
    assert 4 in line_map.values()


def test_whitespace_source_section_resolves_without_losing_original_lines(parts,tmp_path):
    path=tmp_path/'spaces.mpd'
    atomic_write(path,mpd(ref(),'cab.ldr '))
    copied,manifest=extract_section(path,'cab.ldr ',namespace='spaces')
    assert manifest['parser_section']=='cab.ldr'
    assert 'spaces-00-cab.ldr' in copied
    assert model_sections(path,'cab.ldr')['sections'][0]['name']=='cab.ldr '
    atomic_write(path,mpd(ref('cab.ldr '),'main.ldr')+mpd(ref(),'cab.ldr '))
    copied,_=extract_section(path,'main.ldr',namespace='nested')
    _,diagnostics=validate_text(copied,parts)
    assert not [d for d in diagnostics if d['severity']=='error']


def test_reference_cache_is_content_checked_and_keeps_attribution(index,tmp_path,monkeypatch):
    from ldraw_tools.reference_catalog import prepare_reference, record_review, export_example
    row=next(r for r in index.rows('submodels') if r['model']=='a.mpd')
    calls=[]
    def fake_render(path,library,outdir,**kwargs):
        calls.append(str(path));outdir=Path(outdir);outdir.mkdir(parents=True,exist_ok=True)
        for view in kwargs['views']:(outdir/(view+'.png')).write_bytes(b'rendered test fixture')
        (outdir/'leocad-bom.csv').write_text('Part ID,Color Code,Quantity\n3001.dat,4,1\n3003.dat,4,1\n')
        return {}
    monkeypatch.setattr('ldraw_tools.reference_catalog.render',fake_render)
    out=tmp_path/'cards'
    card=prepare_reference(index,row,out,views=['home'],colour=4)
    assert card['bom_comparison']['matches'] and card['assembly_checks_passed']
    assert card['visual_review']=='pending' and not card['contacts_checked']
    assert card['attribution'][0]['author']=='Tests'
    prepare_reference(index,row,out,views=['home'],colour=4)
    assert len(calls)==1
    (out/row['id']/'source.mpd').write_text('changed source')
    prepare_reference(index,row,out,views=['home'],colour=4)
    assert len(calls)==2
    prepare_reference(index,row,out,views=['home','top'],colour=4)
    assert len(calls)==3
    identity=row['id'];card_path=out/identity/'card.json'
    with pytest.raises(ValueError,match='actually inspected'):
        record_review(index,identity,out,decision='adapt',note='Read the view',viewed_views=['back'])
    review=record_review(index,identity,out,decision='adapt',note='Home view shows two separate brick bodies.',viewed_views=['home'])
    assert review['visual_review']['views']==['home']
    target=tmp_path/'example'
    options=dict(title='Two bricks',lesson='Compare brick proportions',placement_notes='Add a shared support')
    with pytest.raises(ValueError,match='contact inspection'):
        export_example(index,identity,out,target,**options)
    # Exercise export policy independently of connector-library availability.
    card=json.loads(card_path.read_text());card['contacts_checked']=True
    card['geometry']={'diagnostics':[{'severity':'error','code':'geometry.incomplete'}]}
    card_path.write_text(json.dumps(card))
    with pytest.raises(ValueError,match='geometry checks'):
        export_example(index,identity,out,target,**options)
    card['geometry']['diagnostics']=[];card_path.write_text(json.dumps(card))
    result=export_example(index,identity,out,target,**options)
    from ldraw_tools.builder import build_plan, load_plan
    text,_,diagnostics=build_plan(load_plan(result['plan']),index.parts)
    assert 'Tests' in text and not [d for d in diagnostics if d['severity']=='error']
    (out/identity/'renders/home.png').write_bytes(b'changed image')
    with pytest.raises(ValueError,match='artifacts changed'):
        export_example(index,identity,out,target,force=True,**options)
    with pytest.raises(ValueError,match='artifacts changed'):
        record_review(index,identity,out,decision='adapt',note='Stale image',viewed_views=['home'])


def test_content_filter_and_translation_invariant_diversity(index):
    report=search(index,'submodels','Test model',engine='fts',pool=10,limit=5,candidates=10)
    assert len(report['results'])==1
    assert len(report['results'][0]['variants'])==1
    assert report['results'][0]['inventory']['physical_placements']==2
    assert not any(r['part']=='s/internal.dat' for r in search(index,'parts','Brick',engine='fts',pool=10,limit=5,candidates=10)['results'])
    assert len(part_suggestions(report['results'])['results'])==2


def test_cached_inventory_rejects_changed_source(index):
    row=next(r for r in index.rows('submodels') if r['model']=='a.mpd')
    index.inventory(row)
    path=Path(row['path']);path.write_text(path.read_text()+'\n0 // changed\n')
    with pytest.raises(ValueError,match='changed'):index.inventory(row)
    report=index.ensure()
    assert report['signature']
    assert index.get(row['id'])['source_sha256']!=row['source_sha256']


def test_jev_results_map_stable_keys_and_keep_scores(index,monkeypatch):
    identity=next(r['id'] for r in index.rows('parts') if r['part']=='3001.dat')
    commands=[]
    class Result:
        returncode=0;stderr=''
        stdout=json.dumps(dict(stats=dict(api_calls=1),exhaustive=False,results=[dict(source=dict(key=dict(id=identity)),score=.81,rank=1)]))
    def execute(command,**kw):commands.append(command);return Result()
    monkeypatch.setattr('ldraw_tools.discovery.subprocess.run',execute)
    report=search(index,'parts','brick',engine='jev',pool=5,limit=2,candidates=5)
    assert commands[0][0]=='jev-rerank' and '--project' not in commands[0]
    assert report['results'][0]['id']==identity and report['results'][0]['score']==.81
    db=Path(commands[0][commands[0].index('--db')+1])
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT count(*) FROM candidates').fetchone()[0]==1
        assert c.execute('PRAGMA table_info(candidates)').fetchone()[5]==1


def test_resolve_engine_needs_executable_and_key():
    found=lambda name:'/usr/bin/jev-rerank'
    assert resolve_engine('auto',env={'TYPESAFE_API_KEY':'k'},which=lambda name:None)==('fts','jev-rerank is not on PATH')
    assert resolve_engine('auto',env={},which=found)[0]=='fts'
    assert 'TYPESAFE_API_KEY' in resolve_engine('auto',env={'TYPESAFE_API_KEY':'  '},which=found)[1]
    assert resolve_engine('auto',env={'TYPESAFE_API_KEY':'k'},which=found)==('jev',None)
    for explicit in ('jev','fts'):
        assert resolve_engine(explicit,env={},which=lambda name:None)==(explicit,None)
    with pytest.raises(ValueError):resolve_engine('semantic')


def jev_available(monkeypatch,key='secret-key'):
    monkeypatch.setattr('ldraw_tools.discovery.shutil.which',lambda name:'/usr/bin/jev-rerank')
    monkeypatch.setenv('TYPESAFE_API_KEY',key)


def test_auto_without_executable_uses_fts_and_records_reason(index,monkeypatch):
    monkeypatch.setattr('ldraw_tools.discovery.shutil.which',lambda name:None)
    monkeypatch.setenv('TYPESAFE_API_KEY','secret-key')
    monkeypatch.setattr('ldraw_tools.discovery.subprocess.run',lambda *a,**k:pytest.fail('Jev must not run'))
    report=search(index,'parts','brick')
    assert (report['engine_requested'],report['engine_used'])==('auto','fts')
    assert 'not on PATH' in report['fallback_reason'] and 'BM25' in report['ranking']
    assert report['results']


def test_auto_with_executable_but_no_key_uses_fts(index,monkeypatch):
    monkeypatch.setattr('ldraw_tools.discovery.shutil.which',lambda name:'/usr/bin/jev-rerank')
    monkeypatch.delenv('TYPESAFE_API_KEY',raising=False)
    monkeypatch.setattr('ldraw_tools.discovery.subprocess.run',lambda *a,**k:pytest.fail('Jev must not run'))
    report=search(index,'parts','brick')
    assert report['engine_used']=='fts' and 'TYPESAFE_API_KEY' in report['fallback_reason']


def test_auto_uses_jev_when_available_and_successful(index,monkeypatch):
    jev_available(monkeypatch)
    identity=next(r['id'] for r in index.rows('parts') if r['part']=='3001.dat')
    class Result:
        returncode=0;stderr=''
        stdout=json.dumps(dict(stats=dict(api_calls=1),exhaustive=False,results=[dict(source=dict(key=dict(id=identity)),score=.7,rank=1)]))
    monkeypatch.setattr('ldraw_tools.discovery.subprocess.run',lambda command,**kw:Result())
    report=search(index,'parts','brick',pool=5,limit=2,candidates=5)
    assert (report['engine_requested'],report['engine_used'],report['engine'])==('auto','jev','jev')
    assert 'fallback_reason' not in report and report['results'][0]['score']==.7


def test_auto_falls_back_once_when_jev_fails(index,monkeypatch):
    jev_available(monkeypatch)
    calls=[]
    class Result:
        returncode=1;stdout='';stderr='x'*500+'\nAuthentication rejected for secret-key'
    monkeypatch.setattr('ldraw_tools.discovery.subprocess.run',lambda command,**kw:calls.append(command) or Result())
    report=search(index,'parts','brick')
    assert len(calls)==1 and report['engine_used']=='fts' and report['engine_requested']=='auto'
    reason=report['fallback_reason']
    assert 'Jev call failed' in reason and 'rejected' in reason and 'secret-key' not in reason and len(reason)<400 and '\n' not in reason
    assert report['results'] and 'fts_rank' in report['results'][0]


def test_auto_falls_back_on_timeout_and_missing_binary(index,monkeypatch):
    import subprocess
    jev_available(monkeypatch)
    for error in (subprocess.TimeoutExpired('jev-rerank',1),FileNotFoundError('jev-rerank')):
        def failing(command,error=error,**kw):raise error
        monkeypatch.setattr('ldraw_tools.discovery.subprocess.run',failing)
        assert search(index,'parts','brick')['engine_used']=='fts'


def test_explicit_engines_are_unchanged(index,monkeypatch):
    jev_available(monkeypatch)
    class Failed:
        returncode=2;stdout='';stderr='denied'
    monkeypatch.setattr('ldraw_tools.discovery.subprocess.run',lambda command,**kw:Failed())
    with pytest.raises(ValueError,match='Jev search failed'):search(index,'parts','brick',engine='jev')
    monkeypatch.setattr('ldraw_tools.discovery.subprocess.run',lambda *a,**k:pytest.fail('FTS must not call Jev'))
    report=search(index,'parts','brick',engine='fts')
    assert (report['engine_requested'],report['engine_used'])==('fts','fts') and 'fallback_reason' not in report


def test_ranked_model_search_has_total_and_pagination(tmp_path):
    root=tmp_path/'models-annotated';root.mkdir()
    database=tmp_path/'ldraw-info.db'
    with sqlite3.connect(database) as c:
        c.execute('CREATE VIRTUAL TABLE MODELS_DESCRIPTIONS_FTS USING fts5(model UNINDEXED, description)')
        c.executemany('INSERT INTO MODELS_DESCRIPTIONS_FTS VALUES(?,?)',[('a','bus and many other things in a building'),('b','bus'),('c','bus on a road')])
    first=search_models('bus',root=root,database=database,limit=1)
    second=search_models('bus',root=root,database=database,limit=1,offset=1)
    assert first['total']==3 and first['truncated']
    assert first['results'][0]['model']=='b'
    assert second['results'][0]['model']!=first['results'][0]['model']


def test_curated_atlas_has_rebuildable_attributed_plans_and_matching_boms(official):
    from ldraw_tools.common import ROOT
    from ldraw_tools.builder import load_plan, build_plan
    from ldraw_tools.examples import search_examples
    from ldraw_tools.external import compare_bom
    atlas=ROOT/'examples/reference-atlas'
    catalog=json.loads((atlas/'catalog.json').read_text())
    references=[row for row in catalog['examples'] if 'reference_id' in row]
    assert len(references)>=20
    assert any(row['key']=='mini-truck-cab' for row in search_examples(family='reference',scale='microscale')['results'])
    for row in references:
        for key in ['model','plan','guide','preview']:assert (atlas/row[key]).is_file()
        folder=(atlas/row['plan']).parent
        card=json.loads((folder/'card.json').read_text())
        assert card['visual_review']['decision'] in {'reuse','adapt'}
        assert set(card['visual_review']['views'])<=set(card['views'])
        assert card['source']['sha256']==row['source_sha256']
        for name,sha in card['artifact_hashes'].items():
            assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==sha
        text,model,diagnostics=build_plan(load_plan(folder/'scene.plan.json'),official)
        assert not [d for d in diagnostics if d['severity']=='error']
        assert compare_bom(model,official,folder/'renders/leocad-bom.csv')['matches']
        for attribution in card['attribution']:
            if attribution.get('author'):assert attribution['author'] in text


@pytest.mark.parametrize('name',['arcade-bay','street-lantern'])
def test_parameterized_recipes_change_real_brick_courses_and_remain_connected(official,name):
    from ldraw_tools.reference_recipes import recipe_plan
    from ldraw_tools.builder import build_plan
    from ldraw_tools.geometry import analyze_geometry
    reports=[]
    for height in [2,7]:
        _,model,diagnostics=build_plan(recipe_plan(name,height=height),official)
        geometry=analyze_geometry(model,official,detail='summary',contacts='all')
        assert not [d for d in diagnostics+geometry['diagnostics'] if d['severity']=='error']
        assert geometry['optimistic_component_count']==1
        reports.append(geometry)
    assert reports[1]['bounds']['min'][1]==reports[0]['bounds']['min'][1]-120
    assert reports[1]['occurrence_count']>reports[0]['occurrence_count']
    with pytest.raises(ValueError):recipe_plan(name,height=0)


def test_recipe_regeneration_clears_stale_previews_and_visual_review(official,tmp_path,monkeypatch):
    from ldraw_tools.reference_recipes import generate_recipe
    monkeypatch.setattr('ldraw_tools.common.get_parts',lambda:official)
    monkeypatch.setattr('sys.argv',['generate.py','--height','2'])
    assert generate_recipe('street-lantern',tmp_path)==0
    (tmp_path/'renders').mkdir()
    (tmp_path/'renders/home.png').write_bytes(b'old image')
    (tmp_path/'visual-review.json').write_text('{"decision":"reuse"}')
    (tmp_path/'render.json').write_text('{}')
    monkeypatch.setattr('sys.argv',['generate.py','--height','7'])
    assert generate_recipe('street-lantern',tmp_path)==0
    assert not (tmp_path/'renders/home.png').exists()
    assert not (tmp_path/'visual-review.json').exists()
    assert not (tmp_path/'render.json').exists()
