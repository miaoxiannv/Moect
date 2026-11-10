import React, {useState} from 'react'
import catalog from '../../ui/wechat-faces.json'

const images = import.meta.glob('../../ui/wechat-faces/*.png', {
  eager: true, query: '?url', import: 'default',
}) as Record<string, string>
const faces = catalog as Record<string, {file: string}>

function InlineFace({token, source}: {token: string, source: string}) {
  const [failed, setFailed] = useState(false)
  if (failed) return <>{token}</>
  return <img src={source} alt={token} title={token} draggable={false}
    className="moect-inline-face" style={{display:'inline-block',width:'1.4em',height:'1.4em',verticalAlign:'-.25em',objectFit:'contain',margin:'0 .04em'}}
    onError={()=>setFailed(true)}/>
}

export function WeChatText({text, links=true}: {text: string, links?: boolean}) {
  const value = String(text ?? '')
  const pattern = /https?:\/\/[^\s<>"']+|\[[^\[\]\r\n]{1,32}\]/g
  const nodes: React.ReactNode[] = []
  let offset = 0
  for (const match of value.matchAll(pattern)) {
    if (match.index! > offset) nodes.push(value.slice(offset, match.index))
    const token = match[0]
    if (/^https?:\/\//.test(token)) {
      nodes.push(links?<a key={match.index} href={token} target="_blank" rel="noopener noreferrer"
        className="inline [overflow-wrap:anywhere] underline decoration-dashed underline-offset-2">{token}</a>:token)
    } else {
      const source = faces[token] && images['../../ui/wechat-faces/' + faces[token].file]
      nodes.push(source?<InlineFace key={`${match.index}:${token}`} token={token} source={source}/>:token)
    }
    offset = match.index! + token.length
  }
  nodes.push(value.slice(offset))
  return <>{nodes}</>
}
