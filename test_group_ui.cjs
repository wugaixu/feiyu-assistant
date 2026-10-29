// Group policy UI: selected users, original group identity and escaped labels.
const {readFileSync}=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const script=readFileSync('app/console.html','utf8').split('<script>')[1].split('</script>')[0].replace(/\nrenderNav\(\);load\(\);/,'');
const nodes=new Map(),posts=[],inputs=[{type:'checkbox',value:'123',checked:true,dataset:{saved:'true'}},{type:'checkbox',value:'456',checked:false,dataset:{saved:'false'}}];
const ctx=vm.createContext({console,AbortController,sessionStorage:{getItem(){},setItem(){}},
 document:{getElementById(id){if(!nodes.has(id))nodes.set(id,{value:'',classList:{add(){},remove(){}}});return nodes.get(id);},querySelectorAll(q){return q.includes('input')?inputs:[];},addEventListener(){}},
 window:{addEventListener(){},confirm:()=>true},setInterval(){},setTimeout(){},clearTimeout(){}});
vm.runInContext(script,ctx);ctx.posts=posts;
vm.runInContext(`api=async(path,opts)=>{globalThis.posts.push({path,body:JSON.parse(opts.body)});return {ok:true};};toast=()=>{};load=async()=>{};groupEditor={group_id:'50001',revision:2};`,ctx);
(async()=>{
 nodes.set('group-enabled',{checked:true});await vm.runInContext('saveGroup()',ctx);
 assert.equal(posts[0].path,'/api/groups');assert.equal(posts[0].body.group_id,'50001');assert.equal(posts[0].body.revision,2);
 assert.deepEqual(Array.from(posts[0].body.members),['123']);assert.equal(posts[0].body.enabled,true);assert.equal(posts[0].body.action,'save');
 vm.runInContext('groupSelectAll(true)',ctx);assert.ok(inputs.every(x=>x.checked));assert.equal(vm.runInContext('settingsDirty',ctx),true);
 vm.runInContext('groupSelectAll(false)',ctx);assert.ok(inputs.every(x=>!x.checked));
 ctx.el={innerHTML:''};vm.runInContext(`groupEditor={group_id:'50001',name:'<img src=x>',members:[{uid:'123',name:'<script>evil</script>'}],selected:['123'],enabled:true};api=async()=>({groups:[]});`,ctx);
 const html=vm.runInContext('groupEditorHTML()',ctx);assert.ok(!html.includes('<img src=x>'));assert.match(html,/&lt;script&gt;/);
 await vm.runInContext('renderGroups(el)',ctx);assert.ok(!ctx.el.innerHTML.includes('批准以上原文发送'));assert.match(ctx.el.innerHTML,/沿用当前智能体/);
 console.log('Group UI: exact group/member save, select-all/clear, dirty edits, escaped names, no approval flow passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
