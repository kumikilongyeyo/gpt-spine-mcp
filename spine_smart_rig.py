from __future__ import annotations
import json, math, os, re
from typing import Iterable

FLEX={"torso","pelvis","upper_arm","lower_arm","upper_leg","lower_leg","fore_leg","hind_leg","tail","wing","hair","cloth","neck"}

def _canon(n:str)->str:return re.sub(r"_+","_",re.sub(r"[^a-z0-9]+","_",n.casefold()).strip("_"))
def _side(n:str)->str:
    v=f"_{_canon(n)}_"
    if any(x in v for x in ("_left_","_l_","_lf_","_lhs_")): return "l"
    if any(x in v for x in ("_right_","_r_","_rt_","_rhs_")): return "r"
    return ""
def semantic_role(name:str)->str:
    n=_canon(name)
    rules=(("eye",("eye","eyelid","pupil")),("mouth",("mouth","lip","teeth","tongue")),("jaw",("jaw","muzzle")),("head",("head","face","skull")),("neck",("neck",)),("hand",("hand","fist","palm")),("lower_arm",("lower_arm","forearm","elbow")),("upper_arm",("upper_arm","bicep","shoulder","arm_upper")),("foot",("foot","shoe","boot")),("lower_leg",("lower_leg","calf","shin","knee")),("upper_leg",("upper_leg","thigh","leg_upper")),("fore_leg",("foreleg","front_leg","frontleg","fore_leg")),("hind_leg",("hindleg","hind_leg","back_leg","rear_leg")),("paw",("paw",)),("hoof",("hoof",)),("pelvis",("pelvis","hip","hips","waist")),("torso",("torso","chest","body","abdomen","belly")),("tail",("tail",)),("wing",("wing","feather")),("hair",("hair","bang","ponytail","beard")),("cloth",("cloth","cape","coat","dress","skirt","sleeve","scarf","ribbon")),("prop",("weapon","sword","gun","shield","staff","coin","logo","title","symbol")))
    for role,aliases in rules:
        if any(a in n for a in aliases): return role
    return "unknown"
def infer_profile(names:Iterable[str],requested:str="auto")->str:
    if requested and requested.casefold() not in {"auto","smart"}: return requested
    roles=[semantic_role(n) for n in names]
    if "wing" in roles:return "winged"
    if any(r in roles for r in ("fore_leg","hind_leg","paw","hoof")):return "quadruped"
    if sum(r in {"upper_arm","lower_arm","hand","upper_leg","lower_leg","foot"} for r in roles)>=2:return "biped"
    return "prop"
def _worlds(bones:list[dict])->dict[str,tuple[float,float]]:
    by={b["name"]:b for b in bones}; out={}
    def w(n):
        if n in out:return out[n]
        b=by[n]; p=b.get("parent"); px,py=w(p) if p in by else (0,0); out[n]=(px+float(b.get("x",0)),py+float(b.get("y",0))); return out[n]
    for n in by:w(n)
    return out
def _entry(atts:dict,name:str):
    s=atts.get(name,{})
    if name in s:return s[name]
    return next((v for v in s.values() if isinstance(v,dict) and v.get("type","region")!="clipping"),None)
def _pivot(cx,cy,w,h,tx,ty):
    dx,dy=tx-cx,ty-cy
    if abs(dx)<1e-6 and abs(dy)<1e-6:return cx,cy
    d=math.hypot(dx,dy); ux,uy=dx/d,dy/d
    a=(w/2)/abs(ux) if abs(ux)>1e-6 else 1e9; b=(h/2)/abs(uy) if abs(uy)>1e-6 else 1e9; t=min(a,b)
    return cx+ux*t,cy+uy*t
def _mesh(e:dict,role:str,ci:int,pi:int|None,cw,pw,maxinf:int)->bool:
    if e.get("type") in {"clipping","boundingbox","path","point"}:return False
    w,h=float(e.get("width",0)),float(e.get("height",0))
    if w<=0 or h<=0:return False
    cx,cy=float(e.get("x",0)),float(e.get("y",0)); seg=3 if role in {"tail","wing","cloth","hair"} else 2 if role in FLEX else 1
    l,r,b,t=cx-w/2,cx+w/2,cy-h/2,cy+h/2; hull=[]
    for i in range(seg+1): hull.append((l+(r-l)*i/seg,t))
    for i in range(1,seg+1): hull.append((r,t+(b-t)*i/seg))
    for i in range(1,seg+1): hull.append((r+(l-r)*i/seg,b))
    for i in range(1,seg): hull.append((l,b+(t-b)*i/seg))
    pts=hull+[(cx,cy)]; c=len(hull); uvs=[]
    for x,y in pts:uvs += [round((x-l)/w,6),round((t-y)/h,6)]
    tris=[]
    for i in range(c):tris += [i,(i+1)%c,c]
    verts=[]; blend=maxinf>1 and pi is not None and pw is not None
    for x,y in pts:
        dist=math.hypot(x,y); pwgt=max(0,min(.42,.42*(1-dist/max(1,min(w,h)*.6)))) if blend else 0; cwgt=1-pwgt; wx,wy=cw[0]+x,cw[1]+y
        if pwgt>.015:verts += [2,ci,round(x,3),round(y,3),round(cwgt,4),pi,round(wx-pw[0],3),round(wy-pw[1],3),round(pwgt,4)]
        else:verts += [1,ci,round(x,3),round(y,3),1]
    path=e.get("path"); keep={k:e[k] for k in ("width","height") if k in e}; e.clear()
    if path:e["path"]=path
    e.update(keep);e.update({"type":"mesh","uvs":uvs,"triangles":tris,"vertices":verts,"hull":c});return True
def _rot(data,clip,bone,vals):
    if bone and clip in data.get("animations",{}):data["animations"][clip].setdefault("bones",{}).setdefault(bone,{})["rotate"]=[({"value":v} if t==0 else {"time":t,"value":v}) for t,v in vals]
def _motion(data,rb,profile):
    done=[]; bf=lambda r,s="":rb.get((r,s)) or rb.get((r,""))
    for clip in ("walk","run"):
        if clip in data.get("animations",{}):
            amp,dur=(23,.8) if clip=="walk" else (34,.52)
            for s,ph in (("l",1),("r",-1)):
                _rot(data,clip,bf("upper_leg",s),[(0,amp*ph),(dur/2,-amp*ph),(dur,amp*ph)]);_rot(data,clip,bf("lower_leg",s),[(0,-8*ph),(dur/2,26*ph),(dur,-8*ph)]);_rot(data,clip,bf("upper_arm",s),[(0,-amp*.65*ph),(dur/2,amp*.65*ph),(dur,-amp*.65*ph)])
            done.append(clip)
    if "attack" in data.get("animations",{}):
        a=bf("upper_arm","r") or bf("upper_arm","l"); f=bf("lower_arm","r") or bf("lower_arm","l"); _rot(data,"attack",a,[(0,-12),(.16,-42),(.31,52),(.55,0)]);_rot(data,"attack",f,[(0,0),(.16,-28),(.31,36),(.55,0)]);done.append("attack")
    body="body" if any(b.get("name")=="body" for b in data.get("bones",[])) else "root"
    if "depth_flip" in data.get("animations",{}):data["animations"]["depth_flip"].setdefault("bones",{}).setdefault(body,{})["scale"]=[{"x":1,"y":1},{"time":.22,"x":.08,"y":.94},{"time":.44,"x":-1,"y":1},{"time":.66,"x":-.08,"y":.94},{"time":.88,"x":1,"y":1}];done.append("depth_flip")
    if "depth_shimmer" in data.get("animations",{}):data["animations"]["depth_shimmer"].setdefault("bones",{}).setdefault(body,{}).update({"rotate":[{"value":-1.2},{"time":.55,"value":1.2},{"time":1.1,"value":-1.2}],"scale":[{"x":.985,"y":1.01},{"time":.55,"x":1.015,"y":.99},{"time":1.1,"x":.985,"y":1.01}]});done.append("depth_shimmer")
    return sorted(set(done))
def enhance(runtime_json:str,profile:str="auto",mesh_quality:str="adaptive",max_influences:int=2,add_ik:bool=False)->dict:
    runtime_json=os.path.abspath(os.path.expanduser(runtime_json)); data=json.load(open(runtime_json,encoding="utf-8")); skins=data.get("skins",[]); atts=(skins.get("default",{}) if isinstance(skins,dict) else next((s for s in skins if s.get("name")=="default"),skins[0] if skins else {}).get("attachments",{})); slots=data.get("slots",[]); bones=data.get("bones",[]); prof=infer_profile([s.get("name","") for s in slots],profile)
    if prof.startswith("prop"):return {"ok":True,"profile":prof,"bones_added":[],"meshes_upgraded":0,"weighted_vertices":False,"roles":{},"motion_upgraded":[],"ik_added":[],"warnings":["smart rig kept prop/symbol topology conservative"]}
    before=_worlds(bones); names={b["name"] for b in bones}; rb={}; meta={}; added=[]; roles={}; candidates=[]
    for s in slots:
        n=s.get("name",""); role=semantic_role(n); e=_entry(atts,n)
        if not n or n.startswith("__") or role in {"unknown","prop"} or not e:continue
        ob=s.get("bone","root"); ow=before.get(ob,(0,0)); w,h=float(e.get("width",0)),float(e.get("height",0)); candidates.append((n,s,role,_side(n),e,ob,(ow[0]+float(e.get("x",0)),ow[1]+float(e.get("y",0))),w,h))
    pr={"eye":"head","mouth":"head","jaw":"head","head":"torso","neck":"torso","hair":"head","upper_arm":"torso","lower_arm":"upper_arm","hand":"lower_arm","pelvis":"torso","upper_leg":"pelvis","lower_leg":"upper_leg","foot":"lower_leg","fore_leg":"torso","hind_leg":"pelvis","paw":"fore_leg","hoof":"fore_leg","wing":"torso","tail":"pelvis","cloth":"torso","torso":"body"}; order={r:i for i,r in enumerate(("torso","pelvis","neck","head","eye","mouth","jaw","upper_arm","lower_arm","hand","upper_leg","lower_leg","foot","fore_leg","hind_leg","paw","hoof","wing","tail","hair","cloth"))};candidates.sort(key=lambda x:order.get(x[2],999))
    def parent(role,side):
        want=pr.get(role,"body"); return ("body" if "body" in names else "root") if want=="body" else rb.get((want,side)) or rb.get((want,"")) or ("head" if want=="head" and "head" in names else ("body" if "body" in names else "root"))
    for n,s,role,side,e,ob,center,w,h in candidates:
        p=parent(role,side); ws=_worlds(bones); pw=ws.get(p,(0,0)); pv=_pivot(center[0],center[1],max(w,1),max(h,1),*pw); bn=f"smart_{_canon(n)}"; j=2
        while bn in names:bn=f"smart_{_canon(n)}_{j}";j+=1
        bones.append({"name":bn,"parent":p,"x":round(pv[0]-pw[0],2),"y":round(pv[1]-pw[1],2),"rotation":0});names.add(bn);added.append(bn);rb.setdefault((role,side),bn);rb.setdefault((role,""),bn);roles[n]=role+(f"_{side}" if side else "");ow=before.get(ob,(0,0))
        for v in atts.get(n,{}).values():
            if isinstance(v,dict) and v.get("type") not in {"clipping","boundingbox","path","point"}:v["x"]=round(ow[0]+float(v.get("x",e.get("x",0)))-pv[0],3);v["y"]=round(ow[1]+float(v.get("y",e.get("y",0)))-pv[1],3)
        s["bone"]=bn;meta[n]={"role":role,"bone":bn,"parent":p}
    ws=_worlds(bones); ik=[]
    if add_ik:
        cons=data.setdefault("ik",[])
        for label,up,lo,end in (("arm","upper_arm","lower_arm","hand"),("leg","upper_leg","lower_leg","foot")):
            for side in ("l","r"):
                u,l,e=rb.get((up,side)),rb.get((lo,side)),rb.get((end,side))
                if not (u and l):continue
                tw=ws.get(e) or ws.get(l); target=f"smart_ik_{label}_{side}_target";bones.append({"name":target,"parent":"root","x":round(tw[0],2),"y":round(tw[1],2)});c=f"smart_ik_{label}_{side}";cons.append({"name":c,"target":target,"bones":[u,l],"mix":1,"bendPositive":True});ik.append(c)
    idx={b["name"]:i for i,b in enumerate(bones)}; mc=0; weighted=False
    if mesh_quality!="none":
        for s in slots:
            n=s.get("name",""); m=meta.get(n)
            if not m:continue
            for e in atts.get(n,{}).values():
                if _mesh(e,m["role"],idx[m["bone"]],idx.get(m["parent"]),ws[m["bone"]],ws.get(m["parent"]),max_influences):mc+=1;weighted=weighted or max_influences>1
    motion=_motion(data,rb,prof);json.dump(data,open(runtime_json,"w",encoding="utf-8"),separators=(",",":"));warn=[] if added else ["no semantic anatomy slots were recognized; check layer naming"]
    return {"ok":True,"profile":prof,"bones_added":added,"meshes_upgraded":mc,"weighted_vertices":weighted,"roles":roles,"motion_upgraded":motion,"ik_added":ik,"warnings":warn}
