"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/main.js'), 'utf8');
const sent = [], statuses = [];
let inFlight = 0, peak = 0, nextStatus = 200;
let response = {status:200,ok:true,json:async()=>({ok:true})};
const context = vm.createContext({Promise, jsonHeaders:()=>({}), goToSignIn(){},
  location:{reload(){}}, termStatus:(text)=>statuses.push(text),
  fetch:async(url,opts)=>{
    if (url !== '/api/terminal/input') return response;
    inFlight++;peak=Math.max(peak,inFlight);
    sent.push(JSON.parse(opts.body));
    await new Promise(resolve=>setTimeout(resolve,5));
    inFlight--;
    return {ok:nextStatus===200,status:nextStatus};
  }});
vm.runInContext(source.slice(source.indexOf('const termState ='), source.indexOf('const termEl =')),context);
vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('/* ------------------------------------------------------------------ */',source.indexOf('async function api('))),context);
vm.runInContext('termState.open=true;termState.connectedChatId=1;',context);
(async()=>{
  const first=vm.runInContext('sendTerminalInput("first")',context);
  const second=vm.runInContext('sendTerminalInput("second")',context);
  await Promise.all([first,second]);
  assert.deepEqual(sent.map(x=>x.data),['first','second']);
  assert.equal(peak,1);
  sent.length=0;
  context.large='a'.repeat(31999)+'🚀'+'b'.repeat(35000)+'\n';
  await vm.runInContext('sendTerminalInput(large)',context);
  assert.equal(sent.map(x=>x.data).join(''),context.large);
  assert.ok(sent.every(x=>x.data.length<=32000 && !/[\uD800-\uDBFF]$/.test(x.data)));
  sent.length=0;nextStatus=409;
  await vm.runInContext('sendTerminalInput("x".repeat(65000))',context);
  assert.equal(sent.length,1);
  assert.match(statuses.at(-1),/Disconnected/);
  nextStatus=200;sent.length=0;
  const old=vm.runInContext('sendTerminalInput("old");termState.connectedChatId=2;sendTerminalInput("new");',context);
  await old;
  assert.deepEqual(sent,[{chat_id:2,data:'new'}]);
  response={status:200,ok:true,json:async()=>{throw new Error('HTML');}};
  await assert.rejects(vm.runInContext('api("/api/chats")',context),/unexpected response/);
  response={status:502,ok:false,json:async()=>{throw new Error('HTML');}};
  await assert.rejects(vm.runInContext('api("/api/chats")',context),error=>error.status===502);
  console.log('6 frontend reliability checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
