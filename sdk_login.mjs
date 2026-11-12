// Native douyin-im lifecycle only. No message sends or credential deletion.
export function loginController(client,publish,state){
 let active=null;
 return {start(initial=false){
  if(active)return {state:'in_progress'};
  if(state.online)return {state:'online'};
  if(!initial&&String(state.note||'').startsWith('qr_required')&&Date.now()/1000-(state.qr_created_at||0)<60)return {state:'waiting_qr'};
  publish({ok:false,online:false,note:'logging_in',qr_created_at:null});
  active=Promise.resolve().then(async()=>{
   if(!initial)await client.logout();
   await client.login();
   const account=client.pickAccount();
   // login() can resolve before QR continuation. Never invent online status.
   if(account.online)publish({ok:true,online:true,uid:account.uid,nickname:account.nickname,note:'',qr_created_at:null});
  }).catch(()=>publish({ok:false,online:false,note:'login_failed_manual_action_required',qr_created_at:null})).finally(()=>{active=null;});
  return {state:'started'};
 }};
}
