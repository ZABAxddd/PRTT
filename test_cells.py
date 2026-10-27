from cells import *
from stage import Stage, Actor, Frame

def lay(count, **slots):
    return CellLayout(count, {int(k[1:]): Placement(v) for k, v in slots.items()})

l = lay(1, s1="sayori"); n, mv = l.set_count(2); assert n == 2 and l.name(1) == "t21", mv; print(mv)
l = lay(1, s1="sayori"); n, mv = l.set_count(3); assert l.name(1) == "t31"; print(mv)
l = lay(2, s1="sayori", s2="monika"); n, mv = l.set_count(3)
assert l.count == 3 and set(l.slots) == {1, 2}; print(mv)
# bajar la cantidad quita a los de la DERECHA, sin alertas
l = lay(3, s1="sayori", s3="monika"); n, mv = l.set_count(2); assert set(l.slots) == {1} and l.slots[1].tag == "sayori", mv; print(mv)
l = lay(4, s1="a", s2="b", s3="c", s4="d"); n, mv = l.set_count(1); assert n == 1 and list(l.slots) == [1] and l.name(1) == "t11", mv
l = lay(3, s1="a", s2="b", s3="c"); n, _ = l.set_count(2); assert n == 2 and set(l.slots) == {1, 2}
l = lay(2, s1="a", s2="b"); assert l.set_count(0)[0] == 0 and not l.slots
l = lay(2, s1="sayori", s2="monika")
l.put(1, Placement("natsuki")); assert l.slots[1].tag == "natsuki" and l.slots[2].tag == "monika"
l.put(2, Placement("natsuki", ("1a",))); assert l.slots[1].tag == "monika" and l.slots[2].tag == "natsuki"
l.swap(1, 2); assert l.slots[1].tag == "natsuki"
l = lay(3, s1="a"); l.swap(1, 3); assert set(l.slots) == {3}
st = Stage(); st.actors["sayori"] = Actor("sayori", ("1a",), count=1, slot=1, pos_name="t11", motion="appear", seq=1)
fr = Frame(0, None, st, [])
l = layout_from_stage(st); assert l.count == 1 and l.slots[1].tag == "sayori"
f2 = apply_layout(fr, l); assert f2.stage.actors["sayori"].motion == "appear"
l.set_count(2); l.put(2, Placement("monika", ("2b",), 5)); l.set_zorder(1, -1)
f3 = apply_layout(fr, l); a = f3.stage.actors
assert a["sayori"].pos_name == "t21" and a["monika"].pos_name == "t22" and a["monika"].zorder == 5
assert [x.tag for x in f3.stage.layers()] == ["sayori", "monika"]
assert st.actors["sayori"].pos_name == "t11"
l = CellLayout(1, {1: Placement("sayori", ("1a",), 1)}); assert to_rpy(l) == "show sayori 1a zorder 1 at t11"
# place_at: elegir el transform explícitamente
l = CellLayout(1, {1: Placement("sayori", ("1a",))})
l.place_at("t22", Placement("monika", ("1b",), 2))                  # 1 -> 2: sayori t11 -> t21, monika en t22
assert l.count == 2 and l.slots[1].tag == "sayori" and l.slots[2].tag == "monika"
assert to_rpy(l) == "show sayori 1a zorder 0 at t21\nshow monika 1b zorder 2 at t22", to_rpy(l)
l.place_at("t33", Placement("yuri", ("2a",)))                       # 2 -> 3: t21 -> t31, t22 -> t32
assert l.count == 3 and l.name(1) == "t31" and l.name(3) == "t33"
l.place_at("t31", Placement("monika", ("1b",), 2))                  # monika ya estaba: se intercambia, no se duplica
assert [p.tag for p in l.occupants()].count("monika") == 1 and l.slots[1].tag == "monika"
# --- transform LIBRE: solo se rechaza lo que no se puede escribir tras un `at`
for bad in ("", "t 21", "t21 hop", "9abc", "at", "zorder", "t21,,hop", "x!"):
    try:
        CellLayout(1, {}).place_at(bad, Placement("x")); raise SystemExit(f"debía fallar: {bad!r}")
    except ValueError:
        pass
l = CellLayout(1, {1: Placement("sayori", ("1a",))})
l.place_at("t45", Placement("monika", ("1b",)), slot=2)             # t45 no existe en la rejilla: se acepta tal cual
assert l.count == 2 and l.slots[2].transform == "t45" and l.slots[1].transform == ""
assert to_rpy(l) == "show sayori 1a zorder 0 at t21\nshow monika 1b zorder 0 at t45", to_rpy(l)
l.place_at("mi_pos", Placement("monika", ("1b",)), slot=2)           # nombre propio: sin reajustar a nadie
assert l.count == 2 and l.name(1) == "t21" and l.at_of(2) == "mi_pos"
l.place_at("t21, hop", Placement("sayori", ("1a",)), slot=1)         # varios transforms (== el estándar + hop)
assert l.slots[1].transform == "t21, hop" and "at t21, hop" in to_rpy(l)
l.place_at("t22", Placement("monika", ("1b",)), slot=2)              # == estándar: se normaliza a ""
assert l.slots[2].transform == ""
l = CellLayout(1, {1: Placement("sayori", ("1a",))})
l.place_at("hf22", Placement("monika", ("1b",)))                     # posición con otro prefijo: reajusta y la conserva
assert l.count == 2 and l.name(1) == "t21" and l.at_of(2) == "hf22", to_rpy(l)
l.set_count(3); assert l.at_of(2) == "hf32" and l.at_of(1) == "t31"  # la cifra sigue a la cantidad
l = CellLayout(2, {1: Placement("sayori", ("1a",)), 2: Placement("monika", ("1b",))})
l.place_at("t31", Placement("yuri", ("1a",)), reflow=False, slot=3)  # literal, sin reajustar: t31 en la celda 3
assert l.at_of(3) == "t31" and l.at_of(1) == "t31"                   # (count pasó a 3: sayori ya es t31)
l = CellLayout(2, {1: Placement("sayori", ("1a",), 0, "mi_pos"), 2: Placement("monika", ("1b",))})
l.swap(1, 2); assert l.slots[2].transform == "" and l.slots[2].tag == "sayori"   # al mover de celda vuelve al estándar
l.place_at("t99", Placement("natsuki"), slot=1)                      # ya no hay límite arbitrario
assert l.at_of(1) == "t99"
# round-trip escenario <-> layout con transforms libres
st = Stage()
st.actors["sayori"] = Actor("sayori", ("1a",), count=2, slot=1, pos_name="hf21", extra_transforms=("hop",), seq=1)
st.actors["monika"] = Actor("monika", ("1b",), count=2, slot=2, pos_name="t22", seq=2)
st.actors["yuri"] = Actor("yuri", ("1a",), count=0, slot=0, pos_name="mi_pos", extra_transforms=("mi_pos",), seq=3)
lay = layout_from_stage(st)
assert lay.at_of(1) == "hf21, hop" and lay.slots[2].transform == "" and lay.at_of(3) == "mi_pos", to_rpy(lay)
assert hide_list(st, lay) == []
lay.remove(3); assert hide_list(st, lay) == ["yuri"]
fr = Frame(0, None, st, [])
lay = layout_from_stage(st)
a2 = apply_layout(fr, lay).stage.actors
assert (a2["sayori"].pos_name, a2["yuri"].count, a2["yuri"].pos_name) == ("hf21", 0, "mi_pos")
assert layout_from_stage(apply_layout(fr, lay).stage) == lay        # estable: no aparecen cambios falsos
mk = lambda count, **slots: CellLayout(count, {int(k[1:]): Placement(v) for k, v in slots.items()})
# arrastrar: vacía -> mueve; ocupada -> el ocupante va a la vacía más próxima
l = mk(4, s1="a", s2="b", s3="c"); l.drag(1, 4); assert set(l.slots) == {2, 3, 4} and l.slots[4].tag == "a"
l = mk(4, s1="a", s2="b", s3="c"); l.drag(1, 3)                      # c se desplaza a la 4 (a 1 de distancia), no a la 1
assert {k: p.tag for k, p in l.slots.items()} == {2: "b", 3: "a", 4: "c"}, l.slots
l = mk(3, s1="a", s2="b"); l.drag(1, 2)                              # la única vacía es de donde sale: equivale a intercambio
assert {k: p.tag for k, p in l.slots.items()} == {1: "b", 2: "a"}
l = mk(4, s1="a", s2="b", s4="d"); l.drag(4, 2)                      # b -> la vacía más próxima a la 2: la 3 (1 de distancia) antes que la 4
assert {k: p.tag for k, p in l.slots.items()} == {1: "a", 2: "d", 3: "b"}, l.slots
l = mk(3, s1="a"); l.slots[1].transform = "mi_pos"; l.drag(1, 3); assert l.slots[3].transform == ""
try:
    mk(2, s1="a").drag(2, 1); raise SystemExit("debía fallar")
except ValueError:
    pass
print(to_rpy(l)); print("OK")
