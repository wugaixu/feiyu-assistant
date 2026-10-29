const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('integrations/comfy_manual/portal.html','utf8').split('<script>')[1].split('</script>')[0].replace(/refresh\(\);setInterval\(refresh,3000\);/,'');
function setup(){
 const nodes=new Map(),created=[];
 const element=()=>{const x={children:[],attrs:{},textContent:'',disabled:false,hidden:false,value:'',className:'',append(...xs){this.children.push(...xs)},replaceChildren(...xs){this.children=xs},setAttribute(k,v){this.attrs[k]=v},addEventListener(){},classList:{toggle(){}}};created.push(x);return x};
 let jobs=[{id:'done',state:'done',prompt:'test',references:0,seed:1},{id:'active',state:'running',prompt:'test',references:0,seed:2}];
 const calls=[];
 const context=vm.createContext({console,confirm:()=>true,FormData:class{},setInterval(){},crypto:{randomUUID(){return 'test'}},URL:{revokeObjectURL(){}},
  document:{getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},createElement:element,querySelectorAll(){return created.filter(x=>x.attrs['data-delete-record'])}},
  fetch:async(path,options)=>{if(path==='/api/jobs')return{ok:true,json:async()=>({jobs,record_delete_version:1})};const body=JSON.parse(options.body);calls.push(body);if(body.action==='delete'){jobs=jobs.filter(x=>x.state==='running');return{ok:true,json:async()=>({changed:1,skipped:1,ids:['done']})}}return{ok:true,json:async()=>({changed:1,ids:body.ids})}}});
 vm.runInContext(source,context);return{nodes,created,calls,context,run:s=>vm.runInContext(s,context)};
}
(async()=>{
 const t=setup();await t.run('refresh()');assert.equal(t.created.filter(x=>x.attrs['data-delete-record']).length,1);
 assert.equal(t.nodes.get('clear-records').disabled,false);
 t.context.confirm=()=>false;await t.run("deleteRecords('done')");assert.equal(t.calls.length,0);
 t.context.confirm=()=>true;await t.run("deleteRecords('done')");assert.equal(t.calls[0].action,'delete');assert.equal(t.nodes.get('undo-delete').hidden,false);assert.match(t.nodes.get('record-status').textContent,/图片已保留/);
 await t.run('undoDelete()');assert.equal(t.calls[1].action,'restore');assert.equal(t.nodes.get('undo-delete').hidden,true);
 const old=setup();old.context.fetch=async()=>({ok:true,json:async()=>({jobs:[]})});await old.run('refresh()');assert.equal(old.nodes.get('clear-records').disabled,true);
 console.log('manual portal UI checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});
