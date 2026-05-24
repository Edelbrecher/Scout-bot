/**
 * hero_task_map.js
 * Shared logic: maps blueprint step titles → hero task IDs
 * Used by blueprint_player.html and hero_tasks.html
 */

// ── Hero task registry (mirrors hero_tasks.html data) ─────────────────
const HT_REGISTRY = {
  // CP Production
  cp1:  {name:"KP-Produktion", sub:"50 KP/h",   res:1500,  xp:25,  vil:'start'},
  cp2:  {name:"KP-Produktion", sub:"100 KP/h",  res:3000,  xp:50,  vil:'start'},
  cp3:  {name:"KP-Produktion", sub:"150 KP/h",  res:4500,  xp:75,  vil:'start'},
  cp4:  {name:"KP-Produktion", sub:"250 KP/h",  res:6000,  xp:100, vil:'start'},
  cp5:  {name:"KP-Produktion", sub:"350 KP/h",  res:7500,  xp:125, vil:'start'},
  cp6:  {name:"KP-Produktion", sub:"500 KP/h",  res:9000,  xp:150, vil:'start'},
  cp7:  {name:"KP-Produktion", sub:"750 KP/h",  res:10500, xp:175, vil:'start'},
  // Population
  pop1: {name:"Bevölkerung", sub:"50",   res:1500,  xp:25,  vil:'start'},
  pop2: {name:"Bevölkerung", sub:"100",  res:3000,  xp:50,  vil:'start'},
  pop3: {name:"Bevölkerung", sub:"150",  res:4500,  xp:75,  vil:'start'},
  pop4: {name:"Bevölkerung", sub:"250",  res:6000,  xp:100, vil:'start'},
  pop5: {name:"Bevölkerung", sub:"350",  res:7500,  xp:125, vil:'start'},
  pop6: {name:"Bevölkerung", sub:"500",  res:9000,  xp:150, vil:'start'},
  pop7: {name:"Bevölkerung", sub:"750",  res:10500, xp:175, vil:'start'},
  // Single fields
  crop1:{name:"Getreidefeld", sub:"Stufe 2",  res:600,  xp:10, vil:'start'},
  crop2:{name:"Getreidefeld", sub:"Stufe 4",  res:1200, xp:20, vil:'start'},
  crop3:{name:"Getreidefeld", sub:"Stufe 7",  res:1800, xp:30, vil:'start'},
  crop4:{name:"Getreidefeld", sub:"Stufe 10", res:2400, xp:40, vil:'start'},
  wood1:{name:"Holzfäller",   sub:"Stufe 2",  res:600,  xp:10, vil:'start'},
  wood2:{name:"Holzfäller",   sub:"Stufe 4",  res:1200, xp:20, vil:'start'},
  wood3:{name:"Holzfäller",   sub:"Stufe 7",  res:1800, xp:30, vil:'start'},
  wood4:{name:"Holzfäller",   sub:"Stufe 10", res:2400, xp:40, vil:'start'},
  clay1:{name:"Lehmgrube",    sub:"Stufe 2",  res:600,  xp:10, vil:'start'},
  clay2:{name:"Lehmgrube",    sub:"Stufe 4",  res:1200, xp:20, vil:'start'},
  clay3:{name:"Lehmgrube",    sub:"Stufe 7",  res:1800, xp:30, vil:'start'},
  clay4:{name:"Lehmgrube",    sub:"Stufe 10", res:2400, xp:40, vil:'start'},
  iron1:{name:"Eisenmine",    sub:"Stufe 2",  res:600,  xp:10, vil:'start'},
  iron2:{name:"Eisenmine",    sub:"Stufe 4",  res:1200, xp:20, vil:'start'},
  iron3:{name:"Eisenmine",    sub:"Stufe 7",  res:1800, xp:30, vil:'start'},
  iron4:{name:"Eisenmine",    sub:"Stufe 10", res:2400, xp:40, vil:'start'},
  // All crop
  allcrop1:{name:"Alle Getreidefelder", sub:"Stufe 2",  res:900,  xp:15, vil:'start'},
  allcrop2:{name:"Alle Getreidefelder", sub:"Stufe 3",  res:1800, xp:30, vil:'start'},
  allcrop3:{name:"Alle Getreidefelder", sub:"Stufe 5",  res:2700, xp:45, vil:'start'},
  allcrop4:{name:"Alle Getreidefelder", sub:"Stufe 8",  res:3600, xp:60, vil:'start'},
  allcrop5:{name:"Alle Getreidefelder", sub:"Stufe 10", res:4500, xp:75, vil:'start'},
  // All wood
  allwood1:{name:"Alle Holzfäller", sub:"Stufe 2",  res:900,  xp:15, vil:'start'},
  allwood2:{name:"Alle Holzfäller", sub:"Stufe 3",  res:1800, xp:30, vil:'start'},
  allwood3:{name:"Alle Holzfäller", sub:"Stufe 5",  res:2700, xp:45, vil:'start'},
  allwood4:{name:"Alle Holzfäller", sub:"Stufe 8",  res:3600, xp:60, vil:'start'},
  allwood5:{name:"Alle Holzfäller", sub:"Stufe 10", res:4500, xp:75, vil:'start'},
  // All clay
  allclay1:{name:"Alle Lehmgruben", sub:"Stufe 2",  res:900,  xp:15, vil:'start'},
  allclay2:{name:"Alle Lehmgruben", sub:"Stufe 3",  res:1800, xp:30, vil:'start'},
  allclay3:{name:"Alle Lehmgruben", sub:"Stufe 5",  res:2700, xp:45, vil:'start'},
  allclay4:{name:"Alle Lehmgruben", sub:"Stufe 8",  res:3600, xp:60, vil:'start'},
  allclay5:{name:"Alle Lehmgruben", sub:"Stufe 10", res:4500, xp:75, vil:'start'},
  // All iron
  alliron1:{name:"Alle Eisenminen", sub:"Stufe 2",  res:900,  xp:15, vil:'start'},
  alliron2:{name:"Alle Eisenminen", sub:"Stufe 3",  res:1800, xp:30, vil:'start'},
  alliron3:{name:"Alle Eisenminen", sub:"Stufe 5",  res:2700, xp:45, vil:'start'},
  alliron4:{name:"Alle Eisenminen", sub:"Stufe 8",  res:3600, xp:60, vil:'start'},
  alliron5:{name:"Alle Eisenminen", sub:"Stufe 10", res:4500, xp:75, vil:'start'},
  // Even growth
  even1:{name:"Even Growth", sub:"1× alle auf 2",  res:300,  xp:5,  vil:'start'},
  even2:{name:"Even Growth", sub:"1× alle auf 4",  res:600,  xp:10, vil:'start'},
  even3:{name:"Even Growth", sub:"1× alle auf 7",  res:900,  xp:15, vil:'start'},
  even4:{name:"Even Growth", sub:"1× alle auf 10", res:1200, xp:20, vil:'start'},
  // All resources to level
  allres1:{name:"Alle Res auf", sub:"Stufe 2",  res:2400,  xp:40,  vil:'start'},
  allres2:{name:"Alle Res auf", sub:"Stufe 4",  res:4800,  xp:80,  vil:'start'},
  allres3:{name:"Alle Res auf", sub:"Stufe 7",  res:7200,  xp:120, vil:'start'},
  allres4:{name:"Alle Res auf", sub:"Stufe 8",  res:9600,  xp:160, vil:'start'},
  allres5:{name:"Alle Res auf", sub:"Stufe 9",  res:12000, xp:200, vil:'start'},
  allres6:{name:"Alle Res auf", sub:"Stufe 10", res:14400, xp:240, vil:'start'},
  // Infrastructure
  wh1: {name:"Lager",        sub:"Stufe 1",  res:300,  xp:5,  vil:'start', lvl:1},
  wh2: {name:"Lager",        sub:"Stufe 3",  res:600,  xp:10, vil:'start', lvl:3},
  wh3: {name:"Lager",        sub:"Stufe 7",  res:900,  xp:15, vil:'start', lvl:7},
  wh4: {name:"Lager",        sub:"Stufe 12", res:1200, xp:20, vil:'start', lvl:12},
  wh5: {name:"Lager",        sub:"Stufe 20", res:1500, xp:25, vil:'start', lvl:20},
  gr1: {name:"Kornspeicher", sub:"Stufe 1",  res:300,  xp:5,  vil:'start', lvl:1},
  gr2: {name:"Kornspeicher", sub:"Stufe 3",  res:600,  xp:10, vil:'start', lvl:3},
  gr3: {name:"Kornspeicher", sub:"Stufe 7",  res:900,  xp:15, vil:'start', lvl:7},
  gr4: {name:"Kornspeicher", sub:"Stufe 12", res:1200, xp:20, vil:'start', lvl:12},
  gr5: {name:"Kornspeicher", sub:"Stufe 20", res:1500, xp:25, vil:'start', lvl:20},
  bar1:{name:"Kaserne",      sub:"Stufe 1",  res:300,  xp:5,  vil:'start', lvl:1},
  bar2:{name:"Kaserne",      sub:"Stufe 3",  res:600,  xp:10, vil:'start', lvl:3},
  bar3:{name:"Kaserne",      sub:"Stufe 7",  res:900,  xp:15, vil:'start', lvl:7},
  bar4:{name:"Kaserne",      sub:"Stufe 12", res:1200, xp:20, vil:'start', lvl:12},
  bar5:{name:"Kaserne",      sub:"Stufe 20", res:1500, xp:25, vil:'start', lvl:20},
  sta1:{name:"Stall",        sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  sta2:{name:"Stall",        sub:"Stufe 3",  res:1200, xp:20, vil:'start', lvl:3},
  sta3:{name:"Stall",        sub:"Stufe 7",  res:1800, xp:30, vil:'start', lvl:7},
  sta4:{name:"Stall",        sub:"Stufe 12", res:2400, xp:40, vil:'start', lvl:12},
  sta5:{name:"Stall",        sub:"Stufe 20", res:3000, xp:50, vil:'start', lvl:20},
  ac1: {name:"Akademie",     sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  ac2: {name:"Akademie",     sub:"Stufe 10", res:1200, xp:20, vil:'start', lvl:10},
  ac3: {name:"Akademie",     sub:"Stufe 20", res:1800, xp:30, vil:'start', lvl:20},
  sm1: {name:"Schmiede",     sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  sm2: {name:"Schmiede",     sub:"Stufe 10", res:1200, xp:20, vil:'start', lvl:10},
  sm3: {name:"Schmiede",     sub:"Stufe 20", res:1800, xp:30, vil:'start', lvl:20},
  th1: {name:"Rathaus",      sub:"Stufe 1",  res:2400, xp:40, vil:'start', lvl:1},
  th2: {name:"Rathaus",      sub:"Stufe 10", res:4800, xp:80, vil:'start', lvl:10},
  th3: {name:"Rathaus",      sub:"Stufe 20", res:7200, xp:120,vil:'start', lvl:20},
  ws1: {name:"Werkstatt",    sub:"Stufe 1",  res:2400, xp:40, vil:'start', lvl:1},
  mb1: {name:"Hauptgebäude", sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  mb2: {name:"Hauptgebäude", sub:"Stufe 3",  res:1200, xp:20, vil:'start', lvl:3},
  mb3: {name:"Hauptgebäude", sub:"Stufe 7",  res:1800, xp:30, vil:'start', lvl:7},
  mb4: {name:"Hauptgebäude", sub:"Stufe 12", res:2400, xp:40, vil:'start', lvl:12},
  mb5: {name:"Hauptgebäude", sub:"Stufe 20", res:3000, xp:50, vil:'start', lvl:20},
  cr1: {name:"Versteck",     sub:"Stufe 1",  res:300,  xp:5,  vil:'start', lvl:1},
  cr2: {name:"Versteck",     sub:"Stufe 3",  res:600,  xp:10, vil:'start', lvl:3},
  cr3: {name:"Versteck",     sub:"Stufe 6",  res:900,  xp:15, vil:'start', lvl:6},
  cr4: {name:"Versteck",     sub:"Stufe 10", res:1200, xp:20, vil:'start', lvl:10},
  mk1: {name:"Marktplatz",   sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  mk2: {name:"Marktplatz",   sub:"Stufe 3",  res:1200, xp:20, vil:'start', lvl:3},
  mk3: {name:"Marktplatz",   sub:"Stufe 7",  res:1800, xp:30, vil:'start', lvl:7},
  mk4: {name:"Marktplatz",   sub:"Stufe 12", res:2400, xp:40, vil:'start', lvl:12},
  mk5: {name:"Marktplatz",   sub:"Stufe 20", res:3000, xp:50, vil:'start', lvl:20},
  em1: {name:"Botschaft",    sub:"Stufe 1",  res:1200, xp:20, vil:'start', lvl:1},
  rp1: {name:"Residenz/Palast", sub:"Stufe 1",  res:1200, xp:20, vil:'start', lvl:1},
  rp2: {name:"Residenz/Palast", sub:"Stufe 3",  res:2400, xp:40, vil:'start', lvl:3},
  rp3: {name:"Residenz/Palast", sub:"Stufe 7",  res:3600, xp:60, vil:'start', lvl:7},
  rp4: {name:"Residenz/Palast", sub:"Stufe 10", res:4800, xp:80, vil:'start', lvl:10},
  rp5: {name:"Residenz/Palast", sub:"Stufe 20", res:6000, xp:100,vil:'start', lvl:20},
  wa1: {name:"Mauer/Wall",   sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  wa2: {name:"Mauer/Wall",   sub:"Stufe 3",  res:1200, xp:20, vil:'start', lvl:3},
  wa3: {name:"Mauer/Wall",   sub:"Stufe 7",  res:1800, xp:30, vil:'start', lvl:7},
  wa4: {name:"Mauer/Wall",   sub:"Stufe 12", res:2400, xp:40, vil:'start', lvl:12},
  wa5: {name:"Mauer/Wall",   sub:"Stufe 20", res:3000, xp:50, vil:'start', lvl:20},
  raly1:{name:"Versammlungsplatz",sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  raly2:{name:"Versammlungsplatz",sub:"Stufe 10", res:1200, xp:20, vil:'start', lvl:10},
  raly3:{name:"Versammlungsplatz",sub:"Stufe 20", res:1800, xp:30, vil:'start', lvl:20},
  saw1:{name:"Sägewerk",     sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  saw2:{name:"Sägewerk",     sub:"Stufe 5",  res:1200, xp:20, vil:'start', lvl:5},
  bry1:{name:"Ziegelei",     sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  bry2:{name:"Ziegelei",     sub:"Stufe 5",  res:1200, xp:20, vil:'start', lvl:5},
  if1: {name:"Eisengießerei",sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  if2: {name:"Eisengießerei",sub:"Stufe 5",  res:1200, xp:20, vil:'start', lvl:5},
  gm1: {name:"Mühle",        sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  gm2: {name:"Mühle",        sub:"Stufe 5",  res:1200, xp:20, vil:'start', lvl:5},
  bk1: {name:"Bäckerei",     sub:"Stufe 1",  res:600,  xp:10, vil:'start', lvl:1},
  bk2: {name:"Bäckerei",     sub:"Stufe 5",  res:1200, xp:20, vil:'start', lvl:5},
  party1:{name:"Fest",       sub:"1. Fest",  res:2400, xp:40, vil:'start'},
  // Settled village
  s_cp1: {name:"KP-Produktion", sub:"50 KP/h",   res:1500,  xp:25,  vil:'settled'},
  s_cp2: {name:"KP-Produktion", sub:"100 KP/h",  res:3000,  xp:50,  vil:'settled'},
  s_cp3: {name:"KP-Produktion", sub:"150 KP/h",  res:4500,  xp:75,  vil:'settled'},
  s_cp4: {name:"KP-Produktion", sub:"250 KP/h",  res:6000,  xp:100, vil:'settled'},
  s_cp5: {name:"KP-Produktion", sub:"350 KP/h",  res:7500,  xp:125, vil:'settled'},
  s_cp6: {name:"KP-Produktion", sub:"500 KP/h",  res:9000,  xp:150, vil:'settled'},
  s_cp7: {name:"KP-Produktion", sub:"750 KP/h",  res:10500, xp:175, vil:'settled'},
  s_pop1:{name:"Bevölkerung", sub:"50",  res:1500,  xp:25,  vil:'settled'},
  s_pop2:{name:"Bevölkerung", sub:"100", res:3000,  xp:50,  vil:'settled'},
  s_pop3:{name:"Bevölkerung", sub:"150", res:4500,  xp:75,  vil:'settled'},
  s_pop4:{name:"Bevölkerung", sub:"250", res:6000,  xp:100, vil:'settled'},
  s_pop5:{name:"Bevölkerung", sub:"350", res:7500,  xp:125, vil:'settled'},
  s_pop6:{name:"Bevölkerung", sub:"500", res:9000,  xp:150, vil:'settled'},
  s_pop7:{name:"Bevölkerung", sub:"750", res:10500, xp:175, vil:'settled'},
  s_even1:{name:"Even Growth", sub:"1× alle auf 2",  res:300,  xp:5,  vil:'settled'},
  s_even2:{name:"Even Growth", sub:"1× alle auf 4",  res:600,  xp:10, vil:'settled'},
  s_even3:{name:"Even Growth", sub:"1× alle auf 7",  res:900,  xp:15, vil:'settled'},
  s_even4:{name:"Even Growth", sub:"1× alle auf 10", res:1200, xp:20, vil:'settled'},
  s_allres1:{name:"Alle Res auf", sub:"Stufe 2",  res:2400,  xp:40,  vil:'settled'},
  s_allres2:{name:"Alle Res auf", sub:"Stufe 4",  res:4800,  xp:80,  vil:'settled'},
  s_allres3:{name:"Alle Res auf", sub:"Stufe 7",  res:7200,  xp:120, vil:'settled'},
  s_allres4:{name:"Alle Res auf", sub:"Stufe 8",  res:9600,  xp:160, vil:'settled'},
  s_allres5:{name:"Alle Res auf", sub:"Stufe 9",  res:12000, xp:200, vil:'settled'},
  s_allres6:{name:"Alle Res auf", sub:"Stufe 10", res:14400, xp:240, vil:'settled'},
  s_wh1: {name:"Lager",           sub:"Stufe 1",  res:300,  xp:5,  vil:'settled', lvl:1},
  s_wh2: {name:"Lager",           sub:"Stufe 10", res:600,  xp:10, vil:'settled', lvl:10},
  s_wh3: {name:"Lager",           sub:"Stufe 20", res:900,  xp:15, vil:'settled', lvl:20},
  s_gr1: {name:"Kornspeicher",    sub:"Stufe 1",  res:300,  xp:5,  vil:'settled', lvl:1},
  s_gr2: {name:"Kornspeicher",    sub:"Stufe 10", res:600,  xp:10, vil:'settled', lvl:10},
  s_gr3: {name:"Kornspeicher",    sub:"Stufe 20", res:900,  xp:15, vil:'settled', lvl:20},
  s_bar1:{name:"Kaserne",         sub:"Stufe 1",  res:300,  xp:5,  vil:'settled', lvl:1},
  s_ac1: {name:"Akademie",        sub:"Stufe 1",  res:600,  xp:10, vil:'settled', lvl:1},
  s_th1: {name:"Rathaus",         sub:"Stufe 1",  res:2400, xp:40, vil:'settled', lvl:1},
  s_th2: {name:"Rathaus",         sub:"Stufe 10", res:4800, xp:80, vil:'settled', lvl:10},
  s_th3: {name:"Rathaus",         sub:"Stufe 20", res:7200, xp:120,vil:'settled', lvl:20},
  s_mb1: {name:"Hauptgebäude",    sub:"Stufe 1",  res:600,  xp:10, vil:'settled', lvl:1},
  s_mb2: {name:"Hauptgebäude",    sub:"Stufe 10", res:1200, xp:20, vil:'settled', lvl:10},
  s_mb3: {name:"Hauptgebäude",    sub:"Stufe 20", res:1800, xp:30, vil:'settled', lvl:20},
  s_cr1: {name:"Versteck",        sub:"Stufe 1",  res:300,  xp:5,  vil:'settled', lvl:1},
  s_cr2: {name:"Versteck",        sub:"Stufe 10", res:600,  xp:10, vil:'settled', lvl:10},
  s_mk1: {name:"Marktplatz",      sub:"Stufe 1",  res:600,  xp:10, vil:'settled', lvl:1},
  s_mk2: {name:"Marktplatz",      sub:"Stufe 10", res:1200, xp:20, vil:'settled', lvl:10},
  s_mk3: {name:"Marktplatz",      sub:"Stufe 20", res:1800, xp:30, vil:'settled', lvl:20},
  s_rp1: {name:"Residenz/Palast", sub:"Stufe 1",  res:1200, xp:20, vil:'settled', lvl:1},
  s_rp2: {name:"Residenz/Palast", sub:"Stufe 10", res:2400, xp:40, vil:'settled', lvl:10},
  s_rp3: {name:"Residenz/Palast", sub:"Stufe 20", res:3600, xp:60, vil:'settled', lvl:20},
  s_wa1: {name:"Mauer/Wall",      sub:"Stufe 1",  res:600,  xp:10, vil:'settled', lvl:1},
  s_wa2: {name:"Mauer/Wall",      sub:"Stufe 10", res:1200, xp:20, vil:'settled', lvl:10},
  s_wa3: {name:"Mauer/Wall",      sub:"Stufe 20", res:1800, xp:30, vil:'settled', lvl:20},
  s_raly1:{name:"Versammlungsplatz",sub:"Stufe 1", res:300,  xp:5,  vil:'settled', lvl:1},
  s_raly2:{name:"Versammlungsplatz",sub:"Stufe 10",res:600,  xp:10, vil:'settled', lvl:10},
  s_raly3:{name:"Versammlungsplatz",sub:"Stufe 20",res:900,  xp:15, vil:'settled', lvl:20},
};

// ── Matching rules ─────────────────────────────────────────────────────
// Each rule: { pattern (regex on title), prefix (task id prefix), tiers [{lvl, id}] }
const HT_RULES = [
  // Single fields
  { pat: /holzfäller|woodcutter|holzf/i,         tiers: [{lvl:2,id:'wood1'},{lvl:4,id:'wood2'},{lvl:7,id:'wood3'},{lvl:10,id:'wood4'}], multi: false },
  { pat: /lehmgrube|clay.pit|lehm/i,              tiers: [{lvl:2,id:'clay1'},{lvl:4,id:'clay2'},{lvl:7,id:'clay3'},{lvl:10,id:'clay4'}], multi: false },
  { pat: /eisenmine|iron.mine|eisen(?!g)/i,       tiers: [{lvl:2,id:'iron1'},{lvl:4,id:'iron2'},{lvl:7,id:'iron3'},{lvl:10,id:'iron4'}], multi: false },
  { pat: /getreidefeld|cropland|crop(?! )/i,      tiers: [{lvl:2,id:'crop1'},{lvl:4,id:'crop2'},{lvl:7,id:'crop3'},{lvl:10,id:'crop4'}], multi: false },
  // All fields
  { pat: /alle.holzf|all.wood/i,                  tiers: [{lvl:2,id:'allwood1'},{lvl:3,id:'allwood2'},{lvl:5,id:'allwood3'},{lvl:8,id:'allwood4'},{lvl:10,id:'allwood5'}], multi: true },
  { pat: /alle.lehmg|all.clay/i,                  tiers: [{lvl:2,id:'allclay1'},{lvl:3,id:'allclay2'},{lvl:5,id:'allclay3'},{lvl:8,id:'allclay4'},{lvl:10,id:'allclay5'}], multi: true },
  { pat: /alle.eisen|all.iron/i,                  tiers: [{lvl:2,id:'alliron1'},{lvl:3,id:'alliron2'},{lvl:5,id:'alliron3'},{lvl:8,id:'alliron4'},{lvl:10,id:'alliron5'}], multi: true },
  { pat: /alle.getreide|all.crop/i,               tiers: [{lvl:2,id:'allcrop1'},{lvl:3,id:'allcrop2'},{lvl:5,id:'allcrop3'},{lvl:8,id:'allcrop4'},{lvl:10,id:'allcrop5'}], multi: true },
  // All resources
  { pat: /alle.res|all.res|komplette.wirt/i,      tiers: [{lvl:2,id:'allres1'},{lvl:4,id:'allres2'},{lvl:7,id:'allres3'},{lvl:8,id:'allres4'},{lvl:9,id:'allres5'},{lvl:10,id:'allres6'}], multi: true },
  // Even growth
  { pat: /even.growth|gleichm|1.+alle/i,          tiers: [{lvl:2,id:'even1'},{lvl:4,id:'even2'},{lvl:7,id:'even3'},{lvl:10,id:'even4'}], multi: true },
  // Buildings
  { pat: /hauptgeb|main.build/i,                  tiers: [{lvl:1,id:'mb1'},{lvl:3,id:'mb2'},{lvl:7,id:'mb3'},{lvl:12,id:'mb4'},{lvl:20,id:'mb5'}], multi: false },
  { pat: /rathaus|town.hall/i,                    tiers: [{lvl:1,id:'th1'},{lvl:10,id:'th2'},{lvl:20,id:'th3'}], multi: false },
  { pat: /kornspeicher|granary/i,                 tiers: [{lvl:1,id:'gr1'},{lvl:3,id:'gr2'},{lvl:7,id:'gr3'},{lvl:12,id:'gr4'},{lvl:20,id:'gr5'}], multi: false },
  { pat: /\blager\b|warehouse/i,                  tiers: [{lvl:1,id:'wh1'},{lvl:3,id:'wh2'},{lvl:7,id:'wh3'},{lvl:12,id:'wh4'},{lvl:20,id:'wh5'}], multi: false },
  { pat: /kaserne|barracks/i,                     tiers: [{lvl:1,id:'bar1'},{lvl:3,id:'bar2'},{lvl:7,id:'bar3'},{lvl:12,id:'bar4'},{lvl:20,id:'bar5'}], multi: false },
  { pat: /\bstall\b|stable/i,                     tiers: [{lvl:1,id:'sta1'},{lvl:3,id:'sta2'},{lvl:7,id:'sta3'},{lvl:12,id:'sta4'},{lvl:20,id:'sta5'}], multi: false },
  { pat: /akademie|academy/i,                     tiers: [{lvl:1,id:'ac1'},{lvl:10,id:'ac2'},{lvl:20,id:'ac3'}], multi: false },
  { pat: /schmiede|smithy/i,                      tiers: [{lvl:1,id:'sm1'},{lvl:10,id:'sm2'},{lvl:20,id:'sm3'}], multi: false },
  { pat: /werkstatt|workshop/i,                   tiers: [{lvl:1,id:'ws1'}], multi: false },
  { pat: /versteck|crann/i,                       tiers: [{lvl:1,id:'cr1'},{lvl:3,id:'cr2'},{lvl:6,id:'cr3'},{lvl:10,id:'cr4'}], multi: false },
  { pat: /marktplatz|market/i,                    tiers: [{lvl:1,id:'mk1'},{lvl:3,id:'mk2'},{lvl:7,id:'mk3'},{lvl:12,id:'mk4'},{lvl:20,id:'mk5'}], multi: false },
  { pat: /botschaft|embassy/i,                    tiers: [{lvl:1,id:'em1'}], multi: false },
  { pat: /residenz|palast|palace|residence/i,     tiers: [{lvl:1,id:'rp1'},{lvl:3,id:'rp2'},{lvl:7,id:'rp3'},{lvl:10,id:'rp4'},{lvl:20,id:'rp5'}], multi: false },
  { pat: /mauer|palisade|wall|stadtmauer/i,       tiers: [{lvl:1,id:'wa1'},{lvl:3,id:'wa2'},{lvl:7,id:'wa3'},{lvl:12,id:'wa4'},{lvl:20,id:'wa5'}], multi: false },
  { pat: /versammlungsplatz|rally.point/i,        tiers: [{lvl:1,id:'raly1'},{lvl:10,id:'raly2'},{lvl:20,id:'raly3'}], multi: false },
  { pat: /sägewerk|sawmill/i,                     tiers: [{lvl:1,id:'saw1'},{lvl:5,id:'saw2'}], multi: false },
  { pat: /ziegelei|brickyard/i,                   tiers: [{lvl:1,id:'bry1'},{lvl:5,id:'bry2'}], multi: false },
  { pat: /eisengießerei|iron.found/i,             tiers: [{lvl:1,id:'if1'},{lvl:5,id:'if2'}], multi: false },
  { pat: /\bmühle\b|grain.mill/i,                 tiers: [{lvl:1,id:'gm1'},{lvl:5,id:'gm2'}], multi: false },
  { pat: /bäckerei|bakery/i,                      tiers: [{lvl:1,id:'bk1'},{lvl:5,id:'bk2'}], multi: false },
  // Parties/Feste
  { pat: /fest|party/i,                           tiers: [{lvl:1,id:'party1'}], multi: false, special: 'party' },
  // Population
  { pat: /bevölkerung|population/i,               tiers: [{lvl:50,id:'pop1'},{lvl:100,id:'pop2'},{lvl:150,id:'pop3'},{lvl:250,id:'pop4'},{lvl:350,id:'pop5'},{lvl:500,id:'pop6'},{lvl:750,id:'pop7'}], multi: false, special: 'pop' },
  // CP Production
  { pat: /kulturpunkt|kp.prod|cp.prod|culture.point/i, tiers: [{lvl:50,id:'cp1'},{lvl:100,id:'cp2'},{lvl:150,id:'cp3'},{lvl:250,id:'cp4'},{lvl:350,id:'cp5'},{lvl:500,id:'cp6'},{lvl:750,id:'cp7'}], multi: false, special: 'cp' },
];

/**
 * Extract a number from a string (target or title).
 * Returns the last/largest number found, or the one after "auf"/"to"/"lvl".
 */
function htExtractLevel(title, target) {
  const combined = (target || '') + ' ' + (title || '');
  // Look for "auf X", "to X", "lvl X", "level X", "stufe X"
  const patterns = [
    /(?:auf|to|stufe|level|lvl)\s*(\d+)/gi,
    /(\d+)/g
  ];
  for (const re of patterns) {
    let m, last = null;
    while ((m = re.exec(combined)) !== null) last = parseInt(m[1]);
    if (last !== null) return last;
  }
  return 0;
}

/**
 * Main function: given a step title + target, return array of matching hero task IDs.
 * Rules: if building upgrades TO level X, mark all tiers where tier.lvl <= X.
 * For multi-field tasks (all wood etc.), same logic.
 */
function matchStepToHeroTasks(title, target) {
  const t = (title || '').toLowerCase();
  const ids = [];
  const level = htExtractLevel(title, target);

  for (const rule of HT_RULES) {
    if (!rule.pat.test(t)) continue;

    if (rule.special === 'party') {
      ids.push('party1');
      continue;
    }

    // For pop/cp tasks the "level" is a threshold value, not a building level
    for (const tier of rule.tiers) {
      if (level >= tier.lvl) ids.push(tier.id);
    }
    break; // only first matching rule
  }

  return ids;
}

/**
 * Compute total res+xp reward for a list of task IDs.
 */
function htRewardsFor(taskIds) {
  let res = 0, xp = 0;
  taskIds.forEach(id => {
    const t = HT_REGISTRY[id];
    if (t) { res += t.res; xp += t.xp; }
  });
  return {res, xp};
}

// ── localStorage helpers ───────────────────────────────────────────────
function htStorageKey(guildId)   { return 'ht_done_'   + guildId; }
function htBpStorageKey(guildId) { return 'ht_bp_'     + guildId; } // tasks marked via blueprint

function htGetDone(guildId) {
  return new Set(JSON.parse(localStorage.getItem(htStorageKey(guildId)) || '[]'));
}
function htGetBp(guildId) {
  return new Set(JSON.parse(localStorage.getItem(htBpStorageKey(guildId)) || '[]'));
}
function htSaveDone(guildId, set) {
  localStorage.setItem(htStorageKey(guildId), JSON.stringify([...set]));
}
function htSaveBp(guildId, set) {
  localStorage.setItem(htBpStorageKey(guildId), JSON.stringify([...set]));
}

/**
 * Called from blueprint_player when a step is toggled.
 * isCompleted: true = step was just checked, false = unchecked
 */
function htSyncFromBlueprint(guildId, title, target, isCompleted) {
  const taskIds = matchStepToHeroTasks(title, target);
  if (!taskIds.length) return { res: 0, xp: 0, tasks: [] };

  const done = htGetDone(guildId);
  const bp   = htGetBp(guildId);
  const newTasks = [];

  taskIds.forEach(id => {
    if (isCompleted) {
      if (!done.has(id)) newTasks.push(id);
      done.add(id);
      bp.add(id);
    } else {
      // Only remove from done if it was marked via blueprint (not manually)
      if (bp.has(id)) {
        done.delete(id);
        bp.delete(id);
      }
    }
  });

  htSaveDone(guildId, done);
  htSaveBp(guildId, bp);

  return htRewardsFor(newTasks);
}
