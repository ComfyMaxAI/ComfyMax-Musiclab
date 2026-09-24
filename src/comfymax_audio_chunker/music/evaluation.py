"""Developer metrics for complete, explicitly annotated frame timelines."""
from .model import label
from .temporal import family


def evaluate(expected, predicted, rate, boundary_tolerance_seconds=0.5):
    def check(rows):
        last=0
        for row in rows:
            if type(row['start_frame']) is not int or type(row['end_frame']) is not int or row['start_frame']!=last or row['end_frame']<=last:
                raise ValueError('Evaluation requires complete contiguous frame annotations.')
            last=row['end_frame']
        return last
    total=check(expected)
    if rate<=0 or total<=0 or check(predicted)!=total or boundary_tolerance_seconds<0:
        raise ValueError('Evaluation timelines/rate do not match.')
    root=triad=exact=unknown=0
    j=0
    for truth in expected:
        while j<len(predicted) and predicted[j]['end_frame']<=truth['start_frame']: j+=1
        k=j
        while k<len(predicted) and predicted[k]['start_frame']<truth['end_frame']:
            guess=predicted[k]
            overlap=min(truth['end_frame'],guess['end_frame'])-max(truth['start_frame'],guess['start_frame'])
            same_root=truth['root_pc']==guess['root_pc'] and (truth['root_pc'] is not None or truth['quality']==guess['quality'])
            root+=overlap*same_root
            triad+=overlap*(family(truth)==family(guess))
            exact+=overlap*(label(truth)==label(guess))
            unknown+=overlap*(guess['quality']=='unknown')
            k+=1
    def changes(rows):
        return [(rows[i]['start_frame'],label(rows[i-1]),label(rows[i])) for i in range(1,len(rows)) if label(rows[i-1])!=label(rows[i])]
    expected_changes=changes(expected); predicted_changes=changes(predicted)
    tolerance=rate*boundary_tolerance_seconds
    # One-to-one exact-transition matches: spurious quality flicker counts as false.
    matches=[]; remaining=set(range(len(expected_changes)))
    for frame,left,right in predicted_changes:
        candidates=[i for i in remaining if expected_changes[i][1:]==(left,right) and abs(expected_changes[i][0]-frame)<=tolerance]
        if candidates:
            i=min(candidates,key=lambda i:abs(expected_changes[i][0]-frame)); remaining.remove(i)
            matches.append(abs(expected_changes[i][0]-frame)/rate)
    return dict(overlap_accuracy=exact/total, root_accuracy=root/total,
                triad_family_accuracy=triad/total, exact_quality_accuracy=exact/total,
                unknown_percentage=100*unknown/total, false_chord_changes=len(predicted_changes)-len(matches),
                missed_chord_changes=len(remaining), matched_boundaries=len(matches),
                boundary_timing_error_seconds=sum(matches)/len(matches) if matches else None,
                boundary_tolerance_seconds=boundary_tolerance_seconds,
                false_seventh_regions=sum(r['quality'] in ('7','min7','maj7') and
                    not any(t['root_pc']==r['root_pc'] and t['quality']==r['quality'] and
                            t['start_frame']<r['end_frame'] and t['end_frame']>r['start_frame'] for t in expected) for r in predicted))
