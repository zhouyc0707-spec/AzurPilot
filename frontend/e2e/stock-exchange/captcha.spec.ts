import {expect,test,type Page,type WebSocketRoute} from '@playwright/test'

interface CaptchaOptions {
  callback:(token:string)=>void
  'expired-callback':()=>void
  'error-callback':()=>void
  action?:string
}
type CaptchaWindow=Window&{__captchaState:{widgets:CaptchaOptions[];resets:number[]};__finishCaptchaLoad:()=>void}

// 在生产临时后端同样可运行，不依赖 Go 引擎、公共认证或 demo-main 实例。
async function isolateAuthentication(page:Page,login?:(socket:WebSocketRoute,request:{id:string;params:{body:Record<string,unknown>}})=>void){
  const fees={commission:0,stamp:0,levy:0,borrow:0,financing:0}
  const rules={id:'test',name:'隔离测试',description:'',commissionPPM:0,minCommission:0,stampPPM:0,stampSide:'sell',stampRound:1,levyPPM:0,borrowAnnualPPM:0,financingAnnualPPM:0,imrPPM:500000,mmrPPM:300000,lotSize:1,sellDelayDays:0,cashDelayDays:0,timezone:'Asia/Shanghai',sessions:[],weekdaysOnly:false,holidays:[],quoteTTLSeconds:86400,source:'test'}
  await page.routeWebSocket('**/api/v1/ws',socket=>{
    const server=socket.connectToServer()
    socket.onMessage(message=>{
      const request=JSON.parse(String(message))
      const respond=(result:unknown)=>socket.send(JSON.stringify({v:1,type:'response',id:request.id,ok:true,result}))
      if(request.method==='stock.status')respond({url:'https://test.invalid',instance:'testpilot',instanceId:'testpilot',bindingKey:'test',bound:false,authenticated:false,message:'',snapshot:{instance:'testpilot',actionPoints:100,observedAt:1}})
      else if(request.method==='stock.request'){
        if(request.params.path==='/login'&&login){login(socket,request);return}
        if(request.params.method!=='GET')throw new Error('验证码夹具禁止真实注册或交易')
        const data=request.params.path==='/meta'?{name:'茗喵证券交易所',domain:'test.invalid',mock:true,allowedOrigins:[],initialCash:2000000000,noticeVersion:'test'}:{revision:1,serverTime:1,season:{id:'test',startsAt:0,endsAt:9999999999,settled:false,revenue:fees},rules,delistThreshold:500,open:true,reason:'',stocks:[],rankings:[],lifetimeRevenue:fees,participants:0}
        respond({status:200,data,etag:'',serverTime:1})
      }else server.send(message)
    })
    server.onMessage(message=>socket.send(message))
  })
}

// 夹具只模拟浏览器组件生命周期；真实 Google 组件与 Go Siteverify 由 mock.spec 验证。
function captchaScript(defer=false){return `
  window.__captchaState={widgets:[],resets:[]};
  window.grecaptcha={};
  window.__finishCaptchaLoad=()=>{
    window.grecaptcha={
      render(host,options){
        const id=window.__captchaState.widgets.push(options)-1;
        const button=document.createElement('button');
        button.type='button';button.textContent='测试完成验证';
        button.onclick=()=>options.callback('test-widget-token-'+id);
        host.append(button);return id;
      },
      reset(id){window.__captchaState.resets.push(id);window.__captchaState.widgets[id].callback('stale-reset-token');}
    };
    window.mmexRecaptchaReady();
  };
  ${defer?'':'window.__finishCaptchaLoad();'}
`}

test('注册验证后切换登录，忽略旧回调并在提交和过期后重新验证',async({page})=>{
  const errors:string[]=[],attempts:Record<string,unknown>[]=[]
  let releaseLogin:(()=>void)|undefined
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('https://www.recaptcha.net/recaptcha/api.js**',route=>route.fulfill({contentType:'text/javascript',body:captchaScript(true)}))
  await isolateAuthentication(page,(socket,request)=>{
    attempts.push(request.params.body)
    releaseLogin=()=>socket.send(JSON.stringify({v:1,type:'response',id:request.id,ok:true,result:{status:401,data:{error:{code:'LOGIN_FAILED',message:'模拟认证失败'}},etag:'',serverTime:0}}))
  })
  await page.goto('/#/i/testpilot/stock-exchange')
  const register=page.getByRole('dialog',{name:'开通交易账户'})
  await expect(register).toBeVisible()
  await register.getByLabel('唯一用户名').fill('验证码切换测试')
  await register.getByLabel('密码',{exact:true}).fill('strong-test-password')
  await register.getByLabel('我已阅读注意事项').check()
  await expect(register.getByRole('button',{name:'验证并开通交易账户'})).toBeDisabled()
  // api.js 已加载且 grecaptcha 对象已存在，依赖未就绪时仍不能渲染。
  await expect.poll(()=>page.evaluate(()=>typeof (window as CaptchaWindow).__finishCaptchaLoad)).toBe('function')
  await expect(register.getByRole('button',{name:'测试完成验证'})).toHaveCount(0)
  await page.evaluate(()=>(window as CaptchaWindow).__finishCaptchaLoad())
  await register.getByRole('button',{name:'测试完成验证'}).click()
  await expect(register.getByRole('button',{name:'验证并开通交易账户'})).toBeEnabled()
  await register.getByRole('button',{name:'已有账户登录'}).click()
  const login=page.getByRole('dialog',{name:'登录交易账户'}),submit=login.getByRole('button',{name:'验证并登录',exact:true})
  await expect(submit).toBeDisabled()
  await page.evaluate(()=>(window as CaptchaWindow).__captchaState.widgets[0].callback('stale-register-token'))
  await expect(submit).toBeDisabled()
  expect(await page.evaluate(()=>(window as CaptchaWindow).__captchaState.resets)).toContain(0)
  await login.getByRole('button',{name:'测试完成验证'}).click()
  await expect(submit).toBeEnabled()
  await page.evaluate(()=>(window as CaptchaWindow).__captchaState.widgets.at(-1)!['expired-callback']())
  await expect(submit).toBeDisabled()
  await expect(login).toContainText('验证码已过期')
  await login.getByRole('button',{name:'重试',exact:true}).click()
  await login.getByRole('button',{name:'测试完成验证'}).click()
  await submit.click()
  await expect.poll(()=>attempts.length).toBe(1)
  await expect(login.getByRole('button',{name:'正在验证…'})).toBeDisabled()
  await expect(login.getByRole('button',{name:'开通账户',exact:true})).toBeDisabled()
  await page.evaluate(()=>(window as CaptchaWindow).__captchaState.widgets.at(-1)!.callback('late-in-flight-token'))
  releaseLogin!()
  await expect(login.getByRole('alert')).toContainText('模拟认证失败')
  await expect(submit).toBeDisabled()
  await page.evaluate(()=>{const widgets=(window as CaptchaWindow).__captchaState.widgets;widgets[widgets.length-2].callback('stale-submitted-token')})
  await expect(submit).toBeDisabled()
  await login.getByRole('button',{name:'测试完成验证'}).click()
  await submit.click()
  await expect.poll(()=>attempts.length).toBe(2)
  expect(attempts[0]).toHaveProperty('recaptchaToken')
  expect(attempts[1].recaptchaToken).not.toBe(attempts[0].recaptchaToken)
  expect(await page.evaluate(()=>(window as CaptchaWindow).__captchaState.widgets.some(widget=>widget.action!==undefined))).toBeFalsy()
  releaseLogin!()
  await expect(submit).toBeDisabled()
  expect(errors).toEqual([])
})

test('验证码脚本加载失败后可重试，失败脚本不残留',async({page})=>{
  let attempts=0
  await isolateAuthentication(page)
  await page.route('https://www.recaptcha.net/recaptcha/api.js**',route=>++attempts===1?route.abort('failed'):route.fulfill({contentType:'text/javascript',body:captchaScript()}))
  await page.goto('/#/i/testpilot/stock-exchange')
  const dialog=page.getByRole('dialog',{name:'开通交易账户'})
  await expect(dialog).toContainText('验证码加载失败')
  await dialog.getByRole('button',{name:'重试',exact:true}).click()
  await dialog.getByRole('button',{name:'测试完成验证'}).click()
  await expect(dialog.getByRole('button',{name:'验证并开通交易账户'})).toBeEnabled()
  expect(attempts).toBe(2)
  await expect(page.locator('script[src*="www.recaptcha.net/recaptcha/api.js"]')).toHaveCount(1)
})
