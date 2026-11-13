'use strict';
// Original, simplified 2D silhouettes of a landmark for each destination (viewBox 0 0 200 140).
// Drawn by hand for this project; shown faintly behind each flight card.
(function(){
const P=(d,extra='')=>'<path '+extra+' d="'+d+'"/>',E=d=>P(d,'fill-rule="evenodd"');
const fountain=P('M98 138C96 90 92 40 100 8C108 40 104 90 102 138z')+P('M100 30C120 40 130 70 132 104C124 72 112 52 100 46C88 52 76 72 68 104C70 70 80 40 100 30z','opacity=".55"')+P('M0 138c20-6 40-6 60 0s40 6 60 0 40-6 60 0 20 4 20 4v-2H0z','opacity=".6"');
const gopuram=P('M60 138V118H140V138zM66 118V100H134V118zM72 100V84H128V100zM78 84V68H122V84zM84 68V54H116V68zM90 54V42H110V54zM86 42C90 32 110 32 114 42zM98 32V22h4v10z')+P('M20 138V112h22v26zM158 138V112h22v26z','opacity=".6"');
const skyline=P('M10 138v-30h20v30zM34 138v-50h14v50zM52 138v-36h18v36zM120 138v-44h16v44zM140 138v-26h24v26zM168 138v-60h14v60z')+P('M86 138V70h8v68zM76 70h28l-5-12H81z');
const L={
 pyramids:P('M58 138L112 46l54 92zM128 138l34-58 34 58z','opacity=".85"')+'<circle cx="176" cy="26" r="11"/>'+
  // Sphinx, lying in front of the pyramids, facing left
  P('M4 138v-9l16-2 14-1 4-12 2-12-2-8 2-9 6-4h10l6 5 1 11-2 9-4 6 2 8 24-3 22 1 8 7 4 14z')+P('M42 92h14l-2 4H44z','opacity=".5"'),
 eiffel:P('M100 4l4 26 6 40 10 30 20 38h-18c-6-14-14-22-22-22s-16 8-22 22H60l20-38 10-30 6-40z')+P('M84 66h32v6H84zM74 98h52v7H74z'),
 burj:P('M96 138V60l2-40 2-18 2 18 2 40v78z')+P('M88 138V90h8v48zM104 138V78h8v60zM80 138v-30h8v30zM112 138v-42h8v42z')+P('M14 138v-34h18v34zM36 138v-52h14v52zM142 138v-44h16v44zM162 138v-28h24v28z','opacity=".6"'),
 bigben:P('M88 138V46h24v92zM86 46h28L100 18zM99 18V6h2v12z')+P('M10 138v-30h76v30zM114 138v-26h72v26zM20 108l4-10 4 10zM40 108l4-10 4 10zM60 108l4-10 4 10zM130 112l4-10 4 10zM150 112l4-10 4 10zM170 112l4-10 4 10z','opacity=".7"'),
 mosque:P('M60 138V100h80v38zM62 100a38 34 0 0 1 76 0zM44 138v-24h24v24zM132 138v-24h24v24zM99 66V58h2v8z')+P('M28 138V40l3-14 3 14v98zM166 138V40l3-14 3 14v98zM46 138V58l2-10 2 10v80zM150 138V58l2-10 2 10v80z'),
 charminar:E('M50 138V70h100v68zM80 138v-34a20 20 0 0 1 40 0v34z')+P('M44 138V34h12v104zM144 138V34h12v104zM46 34l4-14 4 14zM146 34l4-14 4 14zM56 70V56h88v14zM88 56a12 10 0 0 1 24 0z'),
 indiagate:E('M60 138V40h80v98zM84 138V84a16 16 0 0 1 32 0v54z')+P('M56 40V30h88v10zM80 30V20h40v10zM92 20a8 6 0 0 1 16 0z'),
 gateway:E('M40 138V60h120v78zM82 138V92a18 18 0 0 1 36 0v46zM48 138v-26h16v26zM136 138v-26h16v26z')+P('M40 60V44h14v16zM146 60V44h14v16zM86 60V48h28v12zM44 44l3-8 3 8zM150 44l3-8 3 8zM90 48a10 8 0 0 1 20 0z'),
 doha:P('M60 138V96h80v42zM74 96V74h52v22zM88 74V58h24v16z')+P('M150 128h40l-6 10h-28zM168 128V80l18 44z')+P('M10 138V80h10v58zM24 138V60c0-8 12-8 12 0v78z','opacity=".6"'),
 fountain,
 kingdom:E('M78 138V40c0-30 44-30 44 0v98zM86 38c0-18 28-18 28 0v8H86z')+P('M140 138l16-108 16 108z')+P('M20 138v-40h16v40zM40 138v-24h20v24z','opacity=".6"'),
 bahrain:P('M50 138c10-38 16-78 20-118h26v118zM150 138c-10-38-16-78-20-118h-26v118z')+P('M96 50h8v3h-8zM96 80h8v3h-8zM96 110h8v3h-8z'),
 columns:P('M40 138v-6h120v6zM46 132V50h10v82zM66 132V50h10v82zM86 132V60h10v72zM106 132V50h10v82zM126 132V70h10v62zM144 132V50h10v82zM42 50v-8h76v8zM140 50v-8h18v8z'),
 cedar:P('M96 138v-30h8v30zM100 16l20 24h-12l24 22h-20l32 26h-28l40 22H44l40-22H56l32-26H68l24-22H80z'),
 umayyad:P('M30 138V100h140v38zM80 100a20 22 0 0 1 40 0zM98 78h4v-8h-4zM150 100V36h10v64zM148 36h14l-7-14zM40 100V60h8v40zM38 60h12l-6-10z'),
 stupa:P('M0 116L14 96l8 6 12-22 8 8 10-18 6 6 14-34 6 10 8-4 10 20 6-6 12 22 8-8 14 24 8-4 14 22 6 2v4H0z','opacity=".42"')+
  P('M72 42L78 52L75 51L72 55L69 52L67 54zM52 70L56 74L53 74L51 77L48 76zM86 48L91 56L88 55L85 58L82 50zM122 76L127 82L124 81L121 84L118 80zM34 80L38 84L35 84L33 86L30 85z','opacity=".95"')+P('M30 138l30-20h80l30 20zM64 118a36 28 0 0 1 72 0zM90 90V80h20v10zM94 80l6-40 6 40zM96 40h8v-6h-8z'),
 prang:P('M88 138l4-38 2-30 3-30 3-34 3 34 3 30 2 30 4 38zM74 138l2-20h48l2 20zM40 138l4-28 4-22 4 22 4 28zM144 138l4-28 4-22 4 22 4 28z'),
 manila:P('M70 138V80h60v58zM70 80l30-20 30 20zM118 80V44h14v36zM120 44a5 6 0 0 1 10 0z')+P('M30 138c4-28 6-48 4-68h3c3 20 2 40-1 68zM35 70C20 60 10 64 4 72c10-8 20-6 31-2C24 54 18 50 12 50c10 0 18 6 24 18 4-14 14-20 24-18-10 2-16 8-22 20 14-8 24-6 30 2-8-6-18-6-30 0z'),
 lotus:P('M97 138V44h6v94zM100 10c-12 10-14 24-8 34h16c6-10 4-24-8-34zM90 138v-10h20v10z')+P('M20 138v-40h16v40zM40 138v-60h12v60zM150 138v-50h14v50zM168 138v-30h18v30z','opacity=".6"'),
 gopuram,
 fishingnet:'<g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M40 134L120 30L180 80M120 30L70 62L180 80M80 132L120 30"/><path d="M180 80Q150 122 100 112Q70 102 70 62" stroke-width="1.6"/></g>'+P('M0 136h200v4H0z')+P('M160 138c3-24 4-40 2-56h3c2 16 1 32-2 56zM163 82c-12-8-20-5-25 2 8-6 16-5 25-2-9-12-14-15-19-15 8 0 14 5 19 15 3-11 11-16 19-14-8 1-13 6-18 16 11-6 19-5 24 1-6-5-14-5-25 0z'),
 vidhana:P('M20 138V96h160v42zM70 96V70h60v26zM78 70a22 20 0 0 1 44 0zM98 50V40h4v10zM30 96V84h20v12zM150 96V84h20v12z'),
 jhulta:E('M50 138V80h100v58zM84 138v-30a16 16 0 0 1 32 0v30z')+P('M56 80V30h10v50zM134 80V30h10v50zM55 30l6-12 6 12zM133 30l6-12 6 12z'),
 dhaka:E('M30 138V60h50v78zM45 100a10 10 0 1 0 20 0a10 10 0 1 0-20 0zM90 138V40h40v98zM100 80l10-16 10 16zM140 138V70h40v68zM150 104h20v12h-20z'),
 canton:P('M88 138c8-38 8-68 4-98 6-10 10-10 16 0-4 30-4 60 4 98zM98 40V6h4v34z')+P('M20 138v-40h16v40zM40 138v-60h14v60zM140 138v-50h14v50zM160 138v-30h20v30z','opacity=".6"'),
 manchester:P('M30 138V90h70v48zM56 90V40h18v50zM54 40l11-24 11 24zM120 138V96h50v42zM178 138V40h8v98z'),
 duomo:P('M30 138V80l70-30 70 30v58z')+P('M36 80V50l3-10 3 10v30zM60 72V40l3-10 3 10v32zM97 52V14l3-12 3 12v38zM134 72V40l3-10 3 10v32zM158 80V50l3-10 3 10v30z'),
 faisal:P('M60 138l40-68 40 68zM40 138V40l3-12 3 12v98zM154 138V40l3-12 3 12v98z'),
 minar:P('M90 138l6-98h8l6 98zM94 40c0-14 12-14 12 0zM99 26V14h2v12zM70 138q30-28 60 0z'),
 onion:P('M70 138V50h22v88zM108 138V50h22v88zM68 50c0-20 26-20 26 0zM106 50c0-20 26-20 26 0zM80 30v-8h2v8zM118 30v-8h2v8zM92 138v-38h16v38z'),
 skyline
};
const MAP=[[/cairo|giza|luxor|alexandria|sohag|assiut/,'pyramids'],[/paris/,'eiffel'],[/dubai|abu ?dhabi|sharjah/,'burj'],[/london|heathrow/,'bigben'],
 [/istanbul|trabzon|bodrum|antalya|ankara|izmir/,'mosque'],[/hyderabad/,'charminar'],[/delhi/,'indiagate'],[/mumbai|bombay/,'gateway'],[/doha/,'doha'],
 [/jeddah|geneva/,'fountain'],[/riyadh/,'kingdom'],[/bahrain|manama/,'bahrain'],[/amman|aqaba/,'columns'],[/beirut/,'cedar'],[/damascus|aleppo/,'umayyad'],
 [/kathmandu/,'stupa'],[/bangkok|phuket/,'prang'],[/manila/,'manila'],[/colombo/,'lotus'],[/chennai|madras|trivandrum|thiruvananthapuram|madurai/,'gopuram'],
 [/kochi|cochin|calicut|kozhikode|mangalore/,'fishingnet'],[/bengaluru|bangalor/,'vidhana'],[/ahmedabad/,'jhulta'],[/dhaka|chittagong/,'dhaka'],
 [/guangzhou|canton/,'canton'],[/manchester/,'manchester'],[/milan/,'duomo'],[/islamabad/,'faisal'],[/lahore/,'minar'],[/munich/,'onion'],
 [/medina|madinah|dammam|taif|tabuk|abha|qassim|gassim/,'kingdom']];
window.landmarkSvg=function(city){
 const c=String(city||'').toLowerCase();if(!c)return '';
 const hit=MAP.find(([re])=>re.test(c)),key=hit?hit[1]:'skyline';
 return '<svg class="lm lm-'+key+'" viewBox="0 0 200 140" preserveAspectRatio="xMaxYMax meet" aria-hidden="true" fill="currentColor">'+L[key]+'</svg>';
};
window.LANDMARK_KEYS=Object.keys(L);
})();
