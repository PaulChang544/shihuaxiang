export default function SectionHead({ idx, cn, en, tail }) {
  return (
    <div className="section-head">
      <span className="idx">{idx}/</span>
      <span className="cn">{cn}</span>
      <span className="en">{en}</span>
      {tail && <span className="tail">{tail}</span>}
    </div>
  )
}
