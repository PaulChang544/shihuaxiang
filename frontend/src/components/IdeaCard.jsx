import Button from '../ui/Button.jsx'
import Icon from '../ui/Icon.jsx'

export default function IdeaCard({ idea, favorited, onFavorite, onConvert }) {
  return (
    <div className="glass glass--hover idea-card">
      <div className="idea-head">
        <span className="idea-type">{idea.activity_type}</span>
        <span className="rating" aria-label={`${idea.star_rating} 星`}>
          {[1, 2, 3, 4, 5].map((i) => (
            <span
              key={i}
              className={`rating__star ${i <= idea.star_rating ? 'rating__star--on' : ''}`}
              style={{ cursor: 'default' }}
            >
              <Icon name="star" size={14} />
            </span>
          ))}
        </span>
      </div>
      <h3 className="idea-name">{idea.name}</h3>
      <p className="idea-highlight">{idea.highlight}</p>
      <div className="idea-hotword">
        <Icon name="trendingUp" size={13} /> {idea.hotword}
        <span className="idea-src">· {idea.hotword_source}</span>
      </div>
      <p className="idea-feas">{idea.feasibility}</p>
      <div className="idea-foot">
        <Button
          size="sm"
          variant={favorited ? 'primary' : 'outline'}
          icon="star"
          onClick={onFavorite}
        >
          {favorited ? '已收藏' : '收藏'}
        </Button>
        <Button size="sm" variant="ghost" icon="flag" onClick={() => onConvert?.(idea)}>
          转为活动
        </Button>
      </div>
    </div>
  )
}
