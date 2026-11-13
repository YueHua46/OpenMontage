#!/usr/bin/env python3
"""Read-only sparse motion diagnostics; never modifies media or assigns pass/fail.

python motion_audit.py clip.mp4 --roi pod:0.1,0.3,0.3,0.3 \
  --roi support:0,0,0.1,0.8 --fps 6 --output review/motion.json

Coordinates are normalized x,y,width,height in the source video. Select a rigid
object and a separate support/background region. Inspect trajectories alongside
readable frame pages. A fit may fail or follow the wrong surface; these metrics
are evidence for review, not semantic proof of motion or physical correctness.
Requires opencv-python and numpy. No generation or repair operations occur.
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def parse_roi(raw):
    name, coords = raw.split(':', 1)
    rect = [float(v) for v in coords.split(',')]
    if len(rect) != 4 or min(rect) < 0 or min(rect[2:]) <= 0 or rect[0]+rect[2] > 1.001 or rect[1]+rect[3] > 1.001:
        raise argparse.ArgumentTypeError('ROI must be name:x,y,width,height within [0,1]')
    return name, rect


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('video', type=Path)
    ap.add_argument('--roi', type=parse_roi, action='append', required=True)
    ap.add_argument('--fps', type=float, default=6)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    if a.fps <= 0:
        ap.error('--fps must be positive')
    cap = cv2.VideoCapture(str(a.video))
    if not cap.isOpened():
        ap.error('cannot open video')
    source_fps = cap.get(cv2.CAP_PROP_FPS)
    if source_fps <= 0:
        ap.error('invalid source frame rate')
    rows, states = {}, {}
    prev = None
    index, next_sample = 0, 0.0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = index/source_fps
        index += 1
        if t + 1e-6 < next_sample:
            continue
        next_sample += 1/a.fps
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h,w = gray.shape
        for name, frac in a.roi:
            x,y,bw,bh = frac
            rect = (int(x*w),int(y*h),int((x+bw)*w),int((y+bh)*h))
            crop = gray[rect[1]:rect[3],rect[0]:rect[2]]
            sharp = float(cv2.Laplacian(crop,cv2.CV_64F).var())
            if prev is None:
                mask = np.zeros_like(gray)
                mask[rect[1]:rect[3],rect[0]:rect[2]]=255
                pts=cv2.goodFeaturesToTrack(gray,maxCorners=180,qualityLevel=.015,minDistance=5,mask=mask)
                states[name] = {'points':pts, 'origin':None if pts is None else pts.copy()}
                rows[name]=[]
            st=states[name]
            fit=None
            pts=st['points']
            if prev is not None and pts is not None and len(pts)>=4:
                moved,status,_=cv2.calcOpticalFlowPyrLK(prev,gray,pts,None,winSize=(25,25),maxLevel=3)
                if moved is not None:
                    back,backstatus,_=cv2.calcOpticalFlowPyrLK(gray,prev,moved,None,winSize=(25,25),maxLevel=3)
                    if back is not None:
                        good=(status.ravel()>0)&(backstatus.ravel()>0)&(np.linalg.norm(back-pts,axis=2).ravel()<1.2)
                        moved=moved[good]
                        origin=st['origin'][good]
                        st['points'],st['origin']=moved,origin
                        if len(moved)>=4:
                            matrix,inliers=cv2.estimateAffinePartial2D(origin,moved,method=cv2.RANSAC,ransacReprojThreshold=2)
                            if matrix is not None:
                                delta=np.median(moved-origin,axis=0).ravel()
                                fit={'dx_px':round(float(delta[0]),3),'dy_px':round(float(delta[1]),3),
                                     'scale':round(float(np.hypot(matrix[0,0],matrix[1,0])),6),
                                     'angle_degrees':round(float(np.degrees(np.arctan2(matrix[1,0],matrix[0,0]))),4),
                                     'inliers':int(inliers.sum())}
            elif prev is None and pts is not None:
                fit={'dx_px':0.0,'dy_px':0.0,'scale':1.0,'angle_degrees':0.0,'inliers':len(pts)}
            rows[name].append({'time':round(t,4),'points':0 if st['points'] is None else len(st['points']),
                               'laplacian_variance':round(sharp,3),'fit':fit})
        prev=gray
    cap.release()
    summaries={}
    for name,data in rows.items():
        valid=[d for d in data if d['fit']]
        summaries[name]={'valid_samples':len(valid),'total_samples':len(data)}
        for key in ['dx_px','dy_px','scale','angle_degrees']:
            values=[d['fit'][key] for d in valid]
            summaries[name][key+'_range']=None if not values else [min(values),max(values)]
    report={'video':str(a.video.resolve()),'source_fps':source_fps,'sampling_fps':a.fps,
            'rois':dict(a.roi),'summary':summaries,'samples':rows,
            'verdict':None,'limitations':'Sparse 2D feature tracks can drift or be lost. Perspective/occlusion/articulation invalidate rigid fits. Sharpness is texture/exposure dependent; compare matching regions at equal display scale. Metrics locate review questions, never establish semantic correctness or authorize video repair.'}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(a.output),'summary':summaries},ensure_ascii=False))


if __name__=='__main__':
    main()
