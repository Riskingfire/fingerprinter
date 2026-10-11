#include <QtWidgets>
#include <QtNetwork>
#include <windows.h>
#include <psapi.h>
#include <zlib.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <numeric>
#include <optional>
#include <map>
#include <set>
#include <vector>
#include <mutex>
#include <atomic>
#include <QRegularExpression>
#include <QNetworkRequest>
#include <QNetworkReply>
#include <QHostAddress>
#include <QFileSystemWatcher>

namespace PF {
    constexpr double WindowSec=15.0, TrainStep=7.5, DetectStep=3.0, TrainWarmup=20.0, DetectWarmup=10.0;
    constexpr int MinMoves=60, MinKeys=8, MaxWindows=800, Buckets=12;
    const QString AppDir=QDir::homePath()+"/AppData/fingerprint";
    const QString ProfileDir=AppDir+"/fingerprints";
    const QString Alphabet="0123456789ABCDEFGHJKMNPQRSTVWXYZ";
    const QStringList featureNames=[]() {
        QStringList v= {
            "m_move_ratio","m_speed_mean","m_speed_std","m_speed_p50","m_speed_p90","m_speed_p99","m_speed_cv","m_acc_mean","m_acc_p90","m_jerk_mean","m_turn_mean","m_turn_p90","m_straight","m_stroke_len","m_stroke_dur","m_stroke_rate","m_horiz_ratio"
        };
        for(int i=0;i<8;i++)v<<QString("m_dir%1").arg(i);
        v<<QStringList {
            "c_rate","c_hold_mean","c_hold_std","c_int_mean","c_int_std","c_fast_ratio","c_left_ratio","s_rate","s_mag","k_rate","k_hold_mean","k_hold_std","k_hold_med","k_hold_p90","k_flight_mean","k_flight_std","k_flight_med","k_overlap","k_unique","k_entropy"
        };
        for(int i=0;i<Buckets;i++)v<<QString("k_hold_b%1").arg(i);
        for(int i=0;i<Buckets;i++)v<<QString("k_freq_b%1").arg(i);
        v<<QStringList {
            "m_peak_pos","m_peak_speed","m_submoves","m_stroke_len_cv","m_pause_mean","m_pause_cv","m_reversal_rate","m_jitter","m_click_lat","c_hold_med","c_int_med","c_int_cv","k_hold_cv","k_hold_p10","k_flight_cv","k_flight_p10","k_flight_p90","k_flight_ac1","k_burst_ratio","k_pause_ratio","r_rate","r_time_ratio","r_hold_mean","r_hold_med","r_hold_std","r_hold_p90","r_tap_ratio","r_hold_ratio","r_int_med","r_int_cv","r_fire_ratio","r_fire_lat"
        };
        for(int i=0;i<Buckets;i++)v<<QString("k_flight_b%1").arg(i);
        return v;
    }
    ();
    struct Move {
        double t;
        int x,y;
    };
    struct Button {
        double t;
        int b;
        bool down;
    };
    struct Scroll {
        double t,mag;
    };
    struct KeyEvent {
        double t;
        bool down;
        QString key;
    };
    static std::mutex dataMutex;
    static std::vector<Move> moves;
    static std::vector<Button> buttons;
    static std::vector<Scroll> scrolls;
    static std::vector<KeyEvent> keys;
    static std::map<QString,double> keyDown;
    static std::map<QString,std::vector<double>> keyHolds;
    static std::map<QString,int> keyPresses;
    static std::atomic_bool recording {
        false
    };
    static bool hotkeyDown=false, afkDown=false;
    static QObject* mainWindowObject=nullptr;
    static HHOOK keyboardHook=nullptr, mouseHook=nullptr;
    static qint64 epochMs=0;
    static QPoint lastPoint;
    static bool havePoint=false;
    double nowSec() {
        return (GetTickCount64()-epochMs)/1000.0;
    }
    void pruneEvents(double now) {
        std::lock_guard<std::mutex>l(dataMutex);
        double cutoff=now-20.0;
        auto mi=std::lower_bound(moves.begin(),moves.end(),cutoff,[](const Move&x,double t){return x.t<t;});
        moves.erase(moves.begin(),mi);
        auto bi=std::lower_bound(buttons.begin(),buttons.end(),cutoff,[](const Button&x,double t){return x.t<t;});
        buttons.erase(buttons.begin(),bi);
        auto si=std::lower_bound(scrolls.begin(),scrolls.end(),cutoff,[](const Scroll&x,double t){return x.t<t;});
        scrolls.erase(scrolls.begin(),si);
        auto ki=std::lower_bound(keys.begin(),keys.end(),cutoff,[](const KeyEvent&x,double t){return x.t<t;});
        keys.erase(keys.begin(),ki);
    }
    QString keyFromVk(DWORD vk) {
        if(vk>=0x41&&vk<=0x5a)return QString(QChar(ushort(vk)).toLower());
        if(vk>=0x30&&vk<=0x39)return QString(QChar(ushort(vk)));
        switch(vk) {
            case VK_SPACE:return "space";
            case VK_SHIFT:return "shift";
            case VK_LSHIFT:return "shift_l";
            case VK_RSHIFT:return "shift_r";
            case VK_CONTROL:return "ctrl";
            case VK_LCONTROL:return "ctrl_l";
            case VK_RCONTROL:return "ctrl_r";
            case VK_MENU:return "alt";
            case VK_LMENU:return "alt_l";
            case VK_RETURN:return "enter";
            case VK_ESCAPE:return "esc";
            case VK_TAB:return "tab";
            case VK_CAPITAL:return "caps_lock";
            case VK_NUMLOCK:return "num_lock";
            case VK_SCROLL:return "scroll_lock";
            case VK_INSERT:return "insert";
            case VK_PAUSE:return "pause";
            case VK_F8:return "f8";
            case VK_F9:return "f9";
            case VK_F10:return "f10";
            case VK_F11:return "f11";
            case VK_UP:return "up";
            case VK_DOWN:return "down";
            case VK_LEFT:return "left";
            case VK_RIGHT:return "right";
            case VK_BACK:return "backspace";
            case VK_DELETE:return "delete";
            default: return QString("vk_%1").arg(vk);
        }
    }
    LRESULT CALLBACK keyboardProc(int n,WPARAM wp,LPARAM lp) {
        if(n==HC_ACTION) {
            auto*k=(KBDLLHOOKSTRUCT*)lp;
            bool down=(wp==WM_KEYDOWN||wp==WM_SYSKEYDOWN);
            QString name=keyFromVk(k->vkCode);
            if(name=="f8") {
                if(down&&!hotkeyDown) {
                    hotkeyDown=true;
                    if(mainWindowObject)QMetaObject::invokeMethod(mainWindowObject,"hotkeyToggle",Qt::QueuedConnection);
                }
                if(!down)hotkeyDown=false;
                return CallNextHookEx(keyboardHook,n,wp,lp);
            }
            if(name=="num_lock") {
                if(down&&!afkDown) {
                    afkDown=true;
                    if(mainWindowObject)QMetaObject::invokeMethod(mainWindowObject,"afkToggle",Qt::QueuedConnection);
                }
                if(!down)afkDown=false;
                return CallNextHookEx(keyboardHook,n,wp,lp);
            }
            if(name=="scroll_lock"||name=="pause"||name=="caps_lock"||name=="insert"||name=="f9"||name=="f10"||name=="f11")return CallNextHookEx(keyboardHook,n,wp,lp);
            if(recording) {
                std::lock_guard<std::mutex> l(dataMutex);
                double t=nowSec();
                if(down) {
                    if(!keyDown.count(name)) {
                        keyDown[name]=t;
                        keyPresses[name]++;
                        keys.push_back({t,true,name});
                    }
                }
                else {
                    keys.push_back({t,false,name});
                    auto it=keyDown.find(name);
                    if(it!=keyDown.end()) {
                        keyHolds[name].push_back(t-it->second);
                        if(keyHolds[name].size()>5000)keyHolds[name].erase(keyHolds[name].begin(),keyHolds[name].begin()+1000);
                        keyDown.erase(it);
                    }
                }
            }
        }
        return CallNextHookEx(keyboardHook,n,wp,lp);
    }
    LRESULT CALLBACK mouseProc(int n,WPARAM wp,LPARAM lp) {
        if(n==HC_ACTION&&recording) {
            auto*m=(MSLLHOOKSTRUCT*)lp;
            std::lock_guard<std::mutex> l(dataMutex);
            double t=nowSec();
            if(wp==WM_MOUSEMOVE) {
                QPoint p(m->pt.x,m->pt.y);
                if(!havePoint||p!=lastPoint) {
                    moves.push_back({t,p.x(),p.y()});
                    lastPoint=p;
                    havePoint=true;
                }
            }
            else if(wp==WM_LBUTTONDOWN||wp==WM_LBUTTONUP||wp==WM_RBUTTONDOWN||wp==WM_RBUTTONUP||wp==WM_MBUTTONDOWN||wp==WM_MBUTTONUP) {
                int b=(wp==WM_LBUTTONDOWN||wp==WM_LBUTTONUP)?0:(wp==WM_RBUTTONDOWN||wp==WM_RBUTTONUP)?1:2;
                bool d=(wp==WM_LBUTTONDOWN||wp==WM_RBUTTONDOWN||wp==WM_MBUTTONDOWN);
                buttons.push_back({t,b,d});
            }
            else if(wp==WM_MOUSEWHEEL||wp==WM_MOUSEHWHEEL) {
                short delta=HIWORD(m->mouseData);
                scrolls.push_back({t,std::abs(delta)/WHEEL_DELTA});
            }
        }
        return CallNextHookEx(mouseHook,n,wp,lp);
    }
    void installHooks() {
        epochMs=GetTickCount64();
        keyboardHook=SetWindowsHookEx(WH_KEYBOARD_LL,keyboardProc,GetModuleHandle(nullptr),0);
        mouseHook=SetWindowsHookEx(WH_MOUSE_LL,mouseProc,GetModuleHandle(nullptr),0);
    }
    void removeHooks() {
        if(keyboardHook)UnhookWindowsHookEx(keyboardHook);
        if(mouseHook)UnhookWindowsHookEx(mouseHook);
        keyboardHook=mouseHook=nullptr;
    }
    struct Profile {
        QString name,created,updated,cls,id;
        double seconds=0;
        int keySlices=0;
        QVector<QVector<double>> windows;
        QStringList windowTags;
        QVector<QJsonObject> windowNums;
        QStringList featureNames;
        QMap<QString,QVector<double>> keyStats;
    };
    QJsonValue num(double d) {
        return std::isfinite(d)?QJsonValue(d):QJsonValue(QJsonValue::Null);
    }
    double quantile(QVector<double> a,double q) {
        if(a.isEmpty())return NAN;
        std::sort(a.begin(),a.end());
        double pos=q*(a.size()-1);
        int lo=int(pos),hi=std::min(lo+1,int(a.size())-1);
        return a[lo]+(a[hi]-a[lo])*(pos-lo);
    }
    double mean(const QVector<double>&v) {
        if(v.isEmpty())return NAN;
        return std::accumulate(v.begin(),v.end(),0.0)/v.size();
    }
    double sd(const QVector<double>&v) {
        if(v.size()<2)return NAN;
        double m=mean(v),s=0;
        for(double x:v)s+=(x-m)*(x-m);
        return std::sqrt(s/v.size());
    }
    QVector<double> extract(double lo,double hi) {
        QVector<double> f(featureNames.size(),NAN);
        auto put=[&](QString n,double v) {
            int i=featureNames.indexOf(n);
            if(i>=0)f[i]=v;
        };
        std::vector<Move> mv;
        std::vector<Button> bt;
        std::vector<Scroll> sc;
        std::vector<KeyEvent> ky;
        {
            std::lock_guard<std::mutex>l(dataMutex);
            for(auto x:moves)if(x.t>=lo&&x.t<=hi)mv.push_back(x);
            for(auto x:buttons)if(x.t>=lo&&x.t<=hi)bt.push_back(x);
            for(auto x:scrolls)if(x.t>=lo&&x.t<=hi)sc.push_back(x);
            for(auto x:keys)if(x.t>=lo&&x.t<=hi)ky.push_back(x);
        }
        double dur=std::max(0.1,hi-lo);
        int keyDownCount=0;
        for(const auto&e:ky)if(e.down)++keyDownCount;
        if(int(mv.size())<MinMoves&&keyDownCount<MinKeys)return f;
        QVector<double> speeds,turns,straight,strokeLen,strokeDur,strokeEnds;
        double totalX=0,totalY=0,moveTime=0;
        QVector<double> dirs(8,0);
        if(mv.size()>=20) {
            for(size_t i=1;i<mv.size();i++) {
                double dt=mv[i].t-mv[i-1].t;
                if(dt<=0||dt>0.12)continue;
                double dx=mv[i].x-mv[i-1].x,dy=mv[i].y-mv[i-1].y,d=std::hypot(dx,dy),s=d/dt;
                totalX+=std::abs(dx);
                totalY+=std::abs(dy);
                if(s>30) {
                    speeds<<s;
                    moveTime+=dt;
                    int bin=int((std::atan2(dy,dx)+M_PI)/(2*M_PI)*8);
                    dirs[std::clamp(bin,0,7)]+=d;
                }
                if(i>1&&dt<0.12) {
                    double ax=mv[i].x-mv[i-1].x,ay=mv[i].y-mv[i-1].y,bx=mv[i-1].x-mv[i-2].x,by=mv[i-1].y-mv[i-2].y;
                    double aa=std::atan2(ay,ax),bb=std::atan2(by,bx),da=std::remainder(aa-bb,2*M_PI);
                    turns<<std::abs(da);
                }
            }
            QVector<double> accs;
            for(int i=1;i<speeds.size();++i)accs<<std::abs(speeds[i]-speeds[i-1])/.02;
            put("m_acc_mean",accs.size()>=5?mean(accs):NAN);
            put("m_acc_p90",accs.size()>=5?quantile(accs,.9):NAN);
            QVector<double>jerks;
            for(int i=1;i<accs.size();++i)jerks<<std::abs(accs[i]-accs[i-1])/.02;
            put("m_jerk_mean",jerks.size()>=5?mean(jerks):NAN);
            QVector<double> strokesStraight,strokesLen,strokesDur,peaks,peakPos,pauses,subs;
            int runStart=-1,prevEnd=-1;
            for(int i=1;i<int(mv.size());++i) {
                double dt=mv[i].t-mv[i-1].t;
                double dist=std::hypot(mv[i].x-mv[i-1].x,mv[i].y-mv[i-1].y);
                bool moving=dt>0&&dt<=.12&&dist/dt>30;
                if(moving&&runStart<0)runStart=i-1;
                if((!moving||i==int(mv.size())-1)&&runStart>=0) {
                    int en=moving&&i==int(mv.size())-1?i:i-1;
                    double path=0,peak=0;
                    int peakAt=0;
                    QVector<double> segSpeeds;
                    for(int j=runStart+1;j<=en;++j) {
                        double d=std::hypot(mv[j].x-mv[j-1].x,mv[j].y-mv[j-1].y);
                        path+=d;
                        double dt2=mv[j].t-mv[j-1].t;
                        double speed=dt2>0?d/dt2:0;
                        segSpeeds<<speed;
                        if(speed>peak) {
                            peak=speed;
                            peakAt=j-runStart-1;
                        }
                    }
                    double sdur=mv[en].t-mv[runStart].t;
                    if(sdur>=.08&&path>=40) {
                        double net=std::hypot(mv[en].x-mv[runStart].x,mv[en].y-mv[runStart].y);
                        strokesStraight<<net/std::max(path,1e-6);
                        strokesLen<<path;
                        strokesDur<<sdur;
                        peaks<<peak;
                        peakPos<<peakAt/double(std::max(1,int(segSpeeds.size())-1));
                        if(segSpeeds.size()>=7) {
                            int dips=0;
                            for(int z=1;z+1<segSpeeds.size();++z)if(segSpeeds[z]<segSpeeds[z-1]&&segSpeeds[z]<=segSpeeds[z+1]&&segSpeeds[z]<.7*peak)dips++;
                            subs<<dips;
                        }
                        strokeEnds<<mv[en].t;
                        if(prevEnd>=0)pauses<<mv[runStart].t-mv[prevEnd].t;
                        prevEnd=en;
                    }
                    runStart=-1;
                }
            }
            put("m_straight",mean(strokesStraight));
            put("m_stroke_len",mean(strokesLen));
            put("m_stroke_dur",mean(strokesDur));
            put("m_stroke_rate",strokesStraight.size()/dur);
            put("m_peak_speed",mean(peaks));
            put("m_peak_pos",mean(peakPos));
            put("m_submoves",mean(subs));
            put("m_stroke_len_cv",strokesLen.size()>=3?sd(strokesLen)/std::max(1e-6,mean(strokesLen)):NAN);
            put("m_pause_mean",pauses.size()>=3?mean(pauses):NAN);
            put("m_pause_cv",pauses.size()>=3?sd(pauses)/std::max(1e-6,mean(pauses)):NAN);
            put("m_turn_mean",mean(turns));
            put("m_turn_p90",quantile(turns,.9));
            put("m_reversal_rate",turns.isEmpty()?NAN:double(std::count_if(turns.begin(),turns.end(),[](double x){return x>2.4;}))/dur);
            put("m_jitter",NAN);
            put("m_click_lat",NAN);
            put("m_move_ratio",moveTime/dur);
            put("m_speed_mean",mean(speeds));
            put("m_speed_std",sd(speeds));
            put("m_speed_p50",quantile(speeds,.5));
            put("m_speed_p90",quantile(speeds,.9));
            put("m_speed_p99",quantile(speeds,.99));
            put("m_speed_cv",sd(speeds)/std::max(1e-6,mean(speeds)));
            put("m_turn_mean",mean(turns));
            put("m_turn_p90",quantile(turns,.9));
            put("m_horiz_ratio",totalX/std::max(1e-6,totalX+totalY));
            for(int i=0;i<8;i++)put(QString("m_dir%1").arg(i),std::accumulate(dirs.begin(),dirs.end(),0.0)>0?dirs[i]/std::accumulate(dirs.begin(),dirs.end(),0.0):NAN);
        }
        QVector<double> clickTimes,clickHolds;
        QMap<int,double> down;
        int left=0;
        for(auto b:bt) {
            if(b.down) {
                clickTimes<<b.t;
                down[b.b]=b.t;
                if(b.b==0)left++;
            }
            else if(down.contains(b.b)) {
                clickHolds<<b.t-down[b.b];
                down.remove(b.b);
            }
        }
        QVector<double> intervals;
        for(int i=1;i<clickTimes.size();i++) {
            double iv=clickTimes[i]-clickTimes[i-1];
            if(iv<3.0)intervals<<iv;
        }
        clickHolds.erase(std::remove_if(clickHolds.begin(),clickHolds.end(),[](double x){return x>=1.5||x<=0;}),clickHolds.end());
        put("c_rate",clickTimes.size()/dur);
        put("c_hold_mean",clickHolds.size()>=3?mean(clickHolds):NAN);
        put("c_hold_std",clickHolds.size()>=3?sd(clickHolds):NAN);
        put("c_hold_med",clickHolds.size()>=3?quantile(clickHolds,.5):NAN);
        put("c_int_mean",intervals.size()>=3?mean(intervals):NAN);
        put("c_int_std",intervals.size()>=3?sd(intervals):NAN);
        put("c_int_med",intervals.size()>=3?quantile(intervals,.5):NAN);
        put("c_int_cv",intervals.size()>=3?sd(intervals)/std::max(1e-6,mean(intervals)):NAN);
        put("c_fast_ratio",intervals.size()>=3?double(std::count_if(intervals.begin(),intervals.end(),[](double x){return x<.25;}))/intervals.size():NAN);
        put("c_left_ratio",clickTimes.isEmpty()?NAN:left/double(clickTimes.size()));
        QVector<double>clickLat;
        for(double ct:clickTimes) {
            double prev=-1;
            for(double se:strokeEnds)if(se<=ct)prev=std::max(prev,se);
            if(prev>=0&&ct-prev<1.0)clickLat<<ct-prev;
        }
        put("m_click_lat",clickLat.size()>=3?mean(clickLat):NAN);
        double sm=0;
        for(auto s:sc)sm+=s.mag;
        put("s_rate",sc.size()/dur);
        put("s_mag",sm/dur);
        QVector<double> keyH,flights;
        QMap<QString,double> kd;
        QMap<QString,int> kc;
        QSet<QString> unique;
        int overlap=0,downs=0;
        QMap<QString,QVector<double>> bucketHold;
        QVector<QString> downNames;
        QVector<double> downTimes;
        for(auto e:ky) {
            if(e.down) {
                downs++;
                unique.insert(e.key);
                downNames<<e.key;
                downTimes<<e.t;
                if(!kd.isEmpty())overlap++;
                kd[e.key]=e.t;
                kc[e.key]++;
            }
            else if(kd.contains(e.key)) {
                double h=e.t-kd[e.key];
                if(h>0&&h<2.0) {
                    keyH<<h;
                    QByteArray kb=e.key.toUtf8();
                    int b=int(crc32(0,reinterpret_cast<const Bytef*>(kb.constData()),uInt(kb.size()))%Buckets);
                    bucketHold[QString::number(b)]<<h;
                }
                kd.remove(e.key);
            }
        }
        for(int i=1;i<downTimes.size();i++)if(downTimes[i]-downTimes[i-1]<2.0)flights<<downTimes[i]-downTimes[i-1];
        put("k_rate",downs/dur);
        put("k_hold_mean",keyH.size()>=4?mean(keyH):NAN);
        put("k_hold_std",keyH.size()>=4?sd(keyH):NAN);
        put("k_hold_med",keyH.size()>=4?quantile(keyH,.5):NAN);
        put("k_hold_p90",keyH.size()>=4?quantile(keyH,.9):NAN);
        put("k_flight_mean",flights.size()>=4?mean(flights):NAN);
        put("k_flight_std",flights.size()>=4?sd(flights):NAN);
        put("k_flight_med",flights.size()>=4?quantile(flights,.5):NAN);
        put("k_overlap",downs>=5?overlap/double(std::max(1,downs)):NAN);
        put("k_unique",downs>=5?unique.size():NAN);
        double ent=0;
        for(auto n:kc) {
            double p=n/double(std::max(1,downs));
            ent-=p*std::log2(std::max(p,1e-12));
        }
        put("k_entropy",downs>=5?ent:NAN);
        QVector<double> bucketCounts(Buckets,0);
        QVector<QVector<double>> bucketFlights(Buckets);
        for(int j=0;j<downNames.size();++j) {
            QByteArray kb=downNames[j].toUtf8();
            int b=int(crc32(0,reinterpret_cast<const Bytef*>(kb.constData()),uInt(kb.size()))%Buckets);
            bucketCounts[b]++;
            if(j>0) {
                double flight=downTimes[j]-downTimes[j-1];
                if(flight<2.0)bucketFlights[b]<<flight;
            }
        }
        for(int i=0;i<Buckets;i++) {
            put(QString("k_hold_b%1").arg(i),bucketHold[QString::number(i)].size()>=2?mean(bucketHold[QString::number(i)]):NAN);
            put(QString("k_freq_b%1").arg(i),downs>=5?bucketCounts[i]/downs:NAN);
            put(QString("k_flight_b%1").arg(i),bucketFlights[i].size()>=2?mean(bucketFlights[i]):NAN);
        }
        put("k_hold_cv",keyH.size()>=4?sd(keyH)/std::max(1e-6,mean(keyH)):NAN);
        put("k_hold_p10",keyH.size()>=4?quantile(keyH,.1):NAN);
        put("k_flight_cv",flights.size()>=4?sd(flights)/std::max(1e-6,mean(flights)):NAN);
        put("k_flight_p10",flights.size()>=4?quantile(flights,.1):NAN);
        put("k_flight_p90",flights.size()>=4?quantile(flights,.9):NAN);
        double ac=NAN;
        if(flights.size()>=8) {
            QVector<double>a,b;
            for(int j=0;j<flights.size()-1;++j) {
                a<<flights[j];
                b<<flights[j+1];
            }
            double ma=mean(a),mb=mean(b),numr=0,da=0,db=0;
            for(int j=0;j<a.size();++j) {
                numr+=(a[j]-ma)*(b[j]-mb);
                da+=(a[j]-ma)*(a[j]-ma);
                db+=(b[j]-mb)*(b[j]-mb);
            }
            if(da>0&&db>0)ac=numr/std::sqrt(da*db);
        }
        put("k_flight_ac1",ac);
        put("k_burst_ratio",flights.size()>=4?double(std::count_if(flights.begin(),flights.end(),[](double x){return x<.15;}))/flights.size():NAN);
        put("k_pause_ratio",flights.size()>=4?double(std::count_if(flights.begin(),flights.end(),[](double x){return x>.8;}))/flights.size():NAN);
        put("m_jitter",NAN);
        put("r_rate",NAN);
        put("r_time_ratio",NAN);
        put("r_hold_mean",NAN);
        put("r_hold_med",NAN);
        put("r_hold_std",NAN);
        put("r_hold_p90",NAN);
        put("r_tap_ratio",NAN);
        put("r_hold_ratio",NAN);
        put("r_int_med",NAN);
        put("r_int_cv",NAN);
        QVector<double> rDowns,rHolds,rIntervals;
        QMap<int,double> rPending;
        for(auto b:bt)if(b.b==1) {
            if(b.down) {
                rDowns<<b.t;
                rPending[1]=b.t;
            }
            else if(rPending.contains(1)) {
                rHolds<<b.t-rPending.value(1);
                rPending.remove(1);
            }
        }
        for(int i=1;i<rDowns.size();++i) {
            double iv=rDowns[i]-rDowns[i-1];
            if(iv<5.0)rIntervals<<iv;
        }
        rHolds.erase(std::remove_if(rHolds.begin(),rHolds.end(),[](double x){return x<=0||x>=10.0;}),rHolds.end());
        double rsum=std::accumulate(rHolds.begin(),rHolds.end(),0.0);
        put("r_rate",rDowns.size()/dur);
        put("r_time_ratio",std::clamp(rsum/dur,0.0,1.0));
        put("r_hold_mean",rHolds.size()>=3?mean(rHolds):NAN);
        put("r_hold_med",rHolds.size()>=3?quantile(rHolds,.5):NAN);
        put("r_hold_std",rHolds.size()>=3?sd(rHolds):NAN);
        put("r_hold_p90",rHolds.size()>=3?quantile(rHolds,.9):NAN);
        put("r_tap_ratio",rHolds.size()>=3?double(std::count_if(rHolds.begin(),rHolds.end(),[](double x){return x<.25;}))/rHolds.size():NAN);
        put("r_hold_ratio",rHolds.size()>=3?double(std::count_if(rHolds.begin(),rHolds.end(),[](double x){return x>=.6;}))/rHolds.size():NAN);
        put("r_int_med",rDowns.size()>=4&&rIntervals.size()>=3?quantile(rIntervals,.5):NAN);
        put("r_int_cv",rDowns.size()>=4&&rIntervals.size()>=3?sd(rIntervals)/std::max(1e-6,mean(rIntervals)):NAN);
        QVector<double>leftTimes;
        for(auto b:bt)if(b.b==0&&b.down)leftTimes<<b.t;
        QVector<double>fireLat;
        int shots=0;
        for(double lt:leftTimes) {
            bool aiming=false;
            for(int i=0;i<rDowns.size()&&i<rHolds.size();++i)if(lt>=rDowns[i]&&lt<=rDowns[i]+rHolds[i])aiming=true;
            if(aiming)shots++;
        }
        put("r_fire_ratio",leftTimes.size()>=3&&!rDowns.isEmpty()?shots/double(leftTimes.size()):NAN);
        for(int i=0;i<rDowns.size()&&i<rHolds.size();++i)for(double lt:leftTimes)if(lt>=rDowns[i]&&lt<=rDowns[i]+rHolds[i]&&lt-rDowns[i]<5.0) {
            fireLat<<lt-rDowns[i];
            break;
        }
        put("r_fire_lat",fireLat.size()>=3?mean(fireLat):NAN);
        return f;
    }
    QString classCode(const QVector<double>&v) {
        auto get=[&](QString n) {
            int i=featureNames.indexOf(n);
            return i>=0&&i<v.size()?v[i]:NAN;
        };
        auto level=[](double x,QVector<double>cuts) {
            if(!std::isfinite(x))return -1;
            int n=0;
            while(n<cuts.size()&&x>cuts[n])++n;
            return n;
        };
        auto seg=[&](QString prefix,double x,QVector<double>cuts) {
            int n=level(x,cuts);
            return prefix+(n<0?QString("??"):QString("%1").arg(n+1,2,10,QChar('0')));
        };
        double kr=get("k_rate"),kh=get("k_hold_med"),ms=get("m_speed_mean"),st=get("m_straight"),click=get("c_rate"),ads=get("r_time_ratio"),scroll=get("s_rate"),stroke=get("m_stroke_rate");
        int k=(level(kr,{1,2.5,4.5})<0||level(kh,{.08,.13,.20,.35})<0)?-1:level(kr,{1,2.5,4.5})*5+level(kh,{.08,.13,.20,.35})+1;
        int m=(level(ms,{600,1200,2200})<0||level(st,{.55,.70,.82,.91})<0)?-1:level(ms,{600,1200,2200})*5+level(st,{.55,.70,.82,.91})+1;
        QString play="Not enough data";
        int avail=0;
        for(double x:{kr,ms,click,ads,scroll})if(std::isfinite(x))++avail;
        if(avail>=2) {
            if(std::isfinite(ads)&&ads>=.28)play="ADS-focused";
            else if(std::isfinite(click)&&click>=4)play="Click-intensive";
            else if(std::isfinite(kr)&&std::isfinite(ms)&&kr>0&&ms<600)play="Keyboard-led";
            else if(std::isfinite(stroke)&&stroke>=2.5&&std::isfinite(st)&&st>=.82)play="Precision / direct-aim";
            else if(std::isfinite(scroll)&&scroll>=1)play="Scroll-intensive";
            else if(std::isfinite(kr)&&std::isfinite(ms)) {
                if(kr>=3&&ms>=1200)play="High-input hybrid";
                else if(kr>=1&&ms>=600)play="Balanced keyboard + mouse";
                else if(ms>=600)play="Mouse-led";
                else if(kr>=1)play="Keyboard-led";
                else play="Low-intensity / mixed";
            }
            else play="Low-intensity / mixed";
        }
        QMap<QString,int>pc {
            {
                "ADS-focused",1
            }, {
                "Click-intensive",2
            }, {
                "Keyboard-led",3
            }, {
                "Mouse-led",4
            }, {
                "Balanced keyboard + mouse",5
            }, {
                "High-input hybrid",6
            }, {
                "Precision / direct-aim",7
            }, {
                "Scroll-intensive",8
            }, {
                "Low-intensity / mixed",9
            }
        };
        QString p=pc.contains(play)?QString("%1").arg(pc[play],2,10,QChar('0')):"??";
        QString kpart=k<0?QString("k??"):QString("k%1").arg(k,2,10,QChar('0'));
        QString mpart=m<0?QString("m??"):QString("m%1").arg(m,2,10,QChar('0'));
        return kpart+mpart+seg("a",st,{.25,.45,.65,.82})+seg("cr",click,{.5,1.5,3,5})+"p"+p+seg("s",scroll,{.05,.25,.75,1.5})+seg("ads",ads,{.01,.08,.20,.40})+seg("ko",get("k_overlap"),{.02,.10,.25,.50})+seg("cv",get("k_flight_cv"),{.25,.50,1,2})+seg("ch",get("c_hold_med"),{.05,.12,.25,.50});
    }
    QStringList automationFlags(const QVector<double>&v) {
        auto g=[&](QString n) {
            int i=featureNames.indexOf(n);
            return i>=0&&i<v.size()?v[i]:NAN;
        };
        QStringList out;
        double cv=g("c_int_cv"),ch=g("c_hold_std"),kh=g("k_hold_std"),kf=g("k_flight_cv"),st=g("m_straight"),ms=g("m_speed_cv");
        if(std::isfinite(cv)&&cv<.06)out<<"Click timing is unusually regular (possible macro)";
        else if(std::isfinite(ch)&&ch<.003)out<<"Click hold times are almost identical (possible macro)";
        if(std::isfinite(kh)&&kh<.003)out<<"Key hold times are almost identical (possible macro)";
        else if(std::isfinite(kf)&&kf<.08)out<<"Key timing is unusually even (possible macro)";
        if(std::isfinite(st)&&std::isfinite(ms)&&st>.985&&ms<.3)out<<"Mouse paths are near-perfect lines at steady speed (possible scripted movement)";
        return out;
    }
    static const double Projection[16][69] =  {
        {
            -0.022063620413537051, 0.094077232930165955, 0.2222086656527428, -0.018242004818778839, 0.078630271979704527, 0.14505724710093426, -0.039888959862384485, 0.11485617871203503, -0.016566365479378328, 0.24604138835101294, -0.024429348516563278, 0.2215665338134396, 0.015142407722030753, -0.18756253018120711, 0.089623279859618474, -0.10293747346473789, -0.20523176244070546, -0.02631288942685639, -0.23571931781011313, -0.088955819169678457, 0.0057291411424065979, -0.14589423967187318, -0.32275211452209845, -0.038696829078813277, -0.052953797956377606, -0.10617688377131847, -0.055406056195779603, 0.11927715452635486, 0.043438447482215883, -0.13267060861329322, 0.034670040684653625, -0.047874610625891878, -0.061754412380284937, 0.052572989547793429, 0.0479220578757567, -0.012206232519722499, 0.021026740815633806, -0.094598930040187421, -0.029096630122516554, 0.15100698626496922, 0.031533598204487714, 0.16122976243201817, 0.23339433109627775, 0.050913476796674406, 0.056917643137937214, 0.021625460870264426, -0.11823502429544551, 0.075320312550708188, -0.08255879641739057, 0.12808270627443744, 0.12519468131605702, -0.11519514177293085, 0.088784731051093177, -0.12082613533729451, 0.045254378773663624, -0.01254687673843374, 0.049953192586785371, -0.13669220626035739, 0.088252766620758621, -0.043513580060377408, -0.019131327855768484, -0.095525911748864836, -0.0081371941184585697, 0.043794502089556285, 0.12171524326783355, 0.0069752131457245497, 0.15716734449964986, 0.10545221738652789, 0.23449936063089036
        },  {
            -0.25528320422771056, 0.083574397885053206, 0.22266534242787178, -0.2099916942755268, -0.075150774276716958, 0.024820503963936816, 0.079199937630017772, -0.0031683450412639332, 0.097623352171182107, -0.028108322216241051, -0.058661367827839567, 0.16798225663364577, -0.14284093053600566, -0.18119921167811678, -0.042586381011103083, 0.0787069965779537, -0.30353199509742296, -0.15056222094108448, -0.082878473915271672, -0.14594704349796114, 0.067562954055527341, 0.17375430965844912, -0.010596650509567058, -0.049523544098439211, 0.19034471358612914, 0.017602960246295856, 0.079349384174327839, -0.026158477555816129, -0.094715945449382652, -0.0059482181750541282, 0.26366748252626548, -0.073535602505213529, -0.065263151400998087, -0.29138420222681954, -0.10128875783882073, 0.07304478333507404, -0.12801278871591237, -0.23054105264469127, 0.15851181588736882, 0.080853188986042976, 0.015225087595207343, -0.034381732111874966, 0.14142100281983208, -0.083068473445436775, 0.054204286268541416, 0.062094899697880776, -0.0043637817790952663, -0.00026528651773940082, -0.16525049230155173, -0.20574154163600944, 0.14258146097959734, 0.15823197782020398, 0.054235477992703209, 0.11150611257719815, 0.0022048148662925709, -0.040468891541253313, 0.013989894720590961, -0.2241249884306033, -0.11060043327982393, -0.20916644930152842, 0.21235525007657272, 0.12889673481033817, -0.053689642624015252, 0.044576071146612911, -0.048249199578379635, 0.053964690292925353, 0.12182656580950603, -0.18014858528395142, 0.074303882336445068
        },  {
            -0.094368650199702683, -0.11161330359611758, 0.096473796878584608, 0.050039131553916129, 0.088414278806199287, 0.12300891792023674, 0.060270371294780758, 0.090725317454432658, -0.013677321728467003, 0.020524011542504786, -0.10800766061513142, -0.076867121572070804, -0.064131085753026562, -0.040606855717500723, 0.006533556931038945, -0.028701540692753373, -0.034947545058820854, 0.11171287377953336, 0.18795782687761844, -0.011674176125872094, 0.19287311301040408, -0.039315565483714884, 0.18823092824675045, 0.050016988012837646, 0.083081693398238898, -0.035944430539744561, 0.24959685188708181, -0.10556412976495422, 0.10478356550501287, -0.17018912852509979, 0.043026831708847232, 0.13836946933096367, -0.21679073067788013, -0.20722728997496059, -0.12406307830071001, 0.10319340632712938, 0.17750483432612776, 0.012180695814609553, -0.1286242272968405, -0.18251291150842444, -0.29158095245607774, 0.15796852507712306, -0.2697070660773544, -0.1796500404415283, -0.33213609141939582, -0.079049325683607133, 0.012812212253197995, 0.10609591029522002, 0.049493183662823938, 0.043478669030842493, 0.038490586204319818, 0.081016504627581115, -0.010861359442982485, 0.15203380632877342, -0.14353319850552573, 0.26116747548280306, 0.05250469465953872, -0.030585789153090702, 0.25276901013058578, 0.052063916943501355, -0.0232298187355106, -0.1730615984747276, -0.081988797019942203, 0.30422789988299609, -0.010687086377311772, 0.11136545408169321, -0.062211855292581178, 0.013729020185478799, 0.049982313901719352
        },  {
            0.18375336694031721, -0.083280611275042063, 0.11209303398058697, 0.088753433664522222, -0.0687525853148446, 0.14826486853334209, 0.17244659081031585, -0.044398718246305147, -0.077858374656709292, 0.0068329895097860652, -0.037692254355594666, -0.15997553402882023, 0.045898767411595182, -0.21969224523446867, 0.16721122278101028, 0.0072530151180324005, -0.020575932746362137, 0.1745598156171351, 0.064960224619971557, 0.16519638934415964, -0.017446970997546364, -0.11690292370683482, -0.074702284182810469, 0.031039860455267101, -0.046902834069365754, 0.16978870109082181, 0.065716686897163729, 0.001270741926566852, -0.16691037322332317, 0.073816898624797969, -0.12387815382050911, -0.18536607015008599, 0.12324333545571978, 0.02946372849206368, -0.093886797867065608, 0.0500033152790011, 0.034890759044585967, 0.057355138007191782, -0.048921649994436142, 0.12536196914641748, -0.031285758357574155, 0.033040763390757517, 0.068107407824393729, 0.24907082630422883, 0.024844012121875415, 0.061899527348589466, 0.030589549383194192, 0.18395400837547321, 0.053571297903901499, -0.11733129205570528, -0.024581595532577868, 0.0029045574188096969, -0.13380124154638515, -0.14568037125250718, 0.17758855327552206, -0.12949170208699992, 0.1106252764806773, 0.020189323973218053, 0.074821906212803227, 0.033100848551901284, 0.08336284486897183, -0.060118683913357077, -0.22921010474308487, 0.0546413549746858, 0.059658284324842471, 0.040728101601222637, 0.036006666494564327, 0.29579484247559218, -0.040499556267434809
        },  {
            0.16374708071882643, -0.16597984843937874, -0.14468643956788571, 0.0034626296504432254, -0.028577590061952589, 0.0097816312288694866, 0.19210529287153991, 0.11905798184986247, 0.27905720085923735, 0.10732555442935492, 0.013685374394958146, 0.0066681497304376192, 0.057333619554482018, -0.097800252577526198, -0.15895327556017716, 0.17269413348416254, 0.14406121906276206, 0.0011672981533822487, -0.25305416050948959, 0.051043142955605664, -0.073855252517475808, -0.020402999658201745, 0.24905676814430619, -0.19729214298482087, 0.11904290016050539, 0.066202834655200576, -0.11683151344268999, 0.16730396315642448, -0.0020515658847697345, 0.027560211377772252, -0.053182136026538679, 0.14761306345551564, 0.085678939209357333, 0.070091758314178507, 0.045923807976719855, -0.0022675125107659167, 0.047853386490303747, 0.045971147353662763, -0.14911680319451695, -0.24601631815958602, -0.2791622318525725, -0.12313205371867901, -0.20007965692244184, -0.20051892774486071, 0.030527490541292251, -0.13751038490162934, -0.054155219873443121, 0.15439629780718309, -0.15606736849284567, 0.041147960354139444, 0.074237907679324408, 0.076884247488056168, -0.11561306125020554, 0.090998302729226418, -0.23079019917950294, -0.018923816498905264, 0.092201800952059418, -0.0084486318038872726, 0.077476175410776588, -0.1065662198244644, 0.051077355138209789, 0.022891707405766196, -0.0082634045793679983, -0.019049827119832587, -0.055599386867616785, 0.032352054015763812, -0.044329125567084578, -0.14851584418436486, -0.22536516209208318
        },  {
            0.22461649169307665, -0.032851953309824398, 0.023132587470928549, 0.241408941928692, -0.054292595058052687, 0.13465644777211319, -0.077457100779018281, -0.053960597258008468, 0.13398374904373681, -0.17951902117645985, -0.046406959075452073, -0.0028359023562591818, 0.19472573073198318, 0.044645084674087669, -0.064248628693198265, 0.075413997479600384, -0.071224307307954426, -0.057552879536194368, -0.057045257702590849, -0.10035567463580156, 0.0046718942546831411, 0.030208577264435807, 0.20057816654037663, -0.12409517448535866, -0.12083629191306949, -0.048837360799958096, -0.1094800714162605, 0.080051479800183997, -0.014902914234613143, -0.13355378058618678, 0.099785488003523035, 0.094430488240354077, -0.43051860097019751, 0.0043267224689100235, 0.046140610646336587, 0.0066109707029746384, -0.16360466056760167, -0.02000267286753693, -0.27013851893330987, -0.12070668220766397, 0.13794154886857332, -0.070922848560564924, 0.059233837417957513, -0.1681602699179392, -0.045999894619435468, 0.10757258258725431, -0.12438951551889672, -0.14205384810158933, 0.12423727795156335, 0.049816154178158142, -0.089228778079745982, -0.19284611160971254, 0.23483915825572343, 0.18379709628727253, -0.0032207305875760063, -0.031337585055512665, -0.031249503394140659, -0.071723827381767594, -0.14043408133285767, 0.19194939083539331, 0.10081915789809019, -0.085556519951254822, 0.321888250087837, -0.064082758898137804, -0.012857805228179647, 0.13897180576441148, -0.10167503344648833, -0.12021369757561959, -0.10634874807949439
        },  {
            0.22932120349947926, -0.020373267160914168, -0.039560170686916779, 0.1176819655703415, 0.1924499231973498, 0.076097570360771319, -0.31954832899577051, 0.087956790478629601, 0.11109367231708669, 0.011122896802190751, 0.10974478373379018, 0.017522226304722506, -0.18937212321751518, -0.11671364464725409, -0.13784167873839451, 0.020128969422646789, 0.12564102419654424, 0.082159149650667604, 0.098572972375866832, -0.16325032390643118, 0.01357263604898532, -0.090365574925334216, -0.17626827756148275, 0.021320559938682575, 0.077996162764355101, 0.041142443819245776, 0.014114583349152419, -0.22345560079573773, 0.032793749542269782, -0.048545984842822208, -0.015224973375563529, -0.07223934658889096, 0.028271044737952982, 0.11763273669104887, -0.1285445975414598, -0.12061487410650766, 0.078546245677947754, 0.087144113755988684, 0.082809914599944814, 0.090008556160774189, -0.23128410371976935, -0.053975835103112706, 0.31591243951809062, -0.11591232691999655, -0.053106044259877014, -0.16161045703661323, -0.19298068487912032, -0.060136036580463431, -0.13299436119874081, -0.24534121356863989, 0.1004196071873671, 0.14086433162242856, 0.02961690869557277, 0.007818937695563551, -0.065527495038777181, -0.033648443703194489, 0.097360057656725882, 0.068391986331869298, 0.077812859902772272, -0.11846850803141543, 0.017747768192866203, 0.18790035226307766, -0.090159464387040075, 0.11834088605065303, -0.12214973297725155, -0.10125197899801287, 0.01246794255177934, 0.070804864207508478, 0.027895725381938137
        },  {
            -0.039342320438924806, 0.065604358253295053, 0.14181807430849028, -0.03282328407654915, -0.050901483520045719, -0.025782890406996335, -0.24191199015824513, -0.093172922508701295, -0.23866868804648744, -0.091351758863994617, 0.14363767056586577, -0.19348671164570924, 0.061292834562149096, 0.13467028365345643, -0.023387163951920427, 0.077720730055650195, -0.090482660090618838, 0.032076376015526868, 0.10784921341819685, 0.18149698169760334, 0.1657181688710081, 0.27832218183928392, 0.0095258348379223257, -0.09276280184951699, -0.38092022689813693, -0.067785675900672096, 0.0773311123769162, -0.0066975398997700516, 0.059389386883481374, -0.080980269390417289, 0.25830609356251527, 0.031047419855473424, -0.10007676681934233, 0.18868345453435345, 0.10585850989247261, -0.060692853375030442, 0.0091409703154872474, -0.20155574786729766, -0.068453482095969534, -0.076782232551117879, 0.23777025282538308, -0.0056770474957231918, 0.035226950888157048, -0.0026879007039832948, -0.092635443929360031, -0.17050435055952734, 0.14247388362925528, 0.14001167267513834, 0.035047550329811834, -0.23531655346901151, 0.055253069671936127, 0.1131174730779592, -0.13001940386647917, -0.0090408326373408437, 0.011659210266695729, -0.097312584195231547, 0.088144824785000436, 0.091589857851606601, 0.18958556584017913, -0.11001511467621228, 0.016788252940908091, -0.057175692774321281, 0.094691214004657853, -0.014512742697042117, 0.039023308790334214, 0.132544131198568, 0.069042477336070204, -0.19481110271251598, -0.17241546841962394
        },  {
            0.088547814335970379, -0.089381508194836409, -0.17457245609811528, -0.019393237155242903, 0.097334819627341751, 0.11923657216408216, 0.10588240219066143, -0.096182245588382884, -0.19296640171134927, -0.24270764264999312, -0.1839621754321708, 0.14984521244844376, 0.15169555540975835, -0.062417613182907984, 0.22024825625163466, -0.022090586657123441, 0.088857737287243069, -0.044009763053823918, 0.033775547173698031, 0.12903910175388977, 0.077143098402656485, 0.063054835626427524, 0.015290849145485459, 0.10440567319699905, -0.17870065583744321, -0.048165993033042441, -0.0057362673305419425, -0.021035282689150375, 0.088647973547547484, 0.057799892378778799, -0.018227102890044228, 0.16007507546192165, 0.0030177177861440336, -0.033147651470181366, -0.10359223260557616, 0.11576160542031556, -0.046203735099978605, -0.12341322926604757, 0.019495415265529215, -0.19906672431377101, 0.01185270304664377, -0.11449185807442495, 0.066640403936608195, -0.00016406524911627849, -0.016763238002177633, 0.0047938253451454051, -0.067035961985804915, -0.32940587713773034, 0.22951777786319816, -0.22657047966753502, 0.076897476807559359, -0.14413090496672576, 0.08819275718213121, 0.046857379605113332, 0.011891594967178076, 0.12537729529596858, -0.10852222033339864, -0.082946921465791121, -0.13776627368550212, -0.048604644654399155, 0.052265964517650761, -0.031136790199301562, -0.055453874636001488, -0.11942731254195811, 0.10937485446090721, 0.043856358561598724, -0.0029870782583636042, 0.14234329953127434, -0.086467089011737761
        },  {
            0.10608186159798491, 0.20184036424980209, 0.26082573575447326, 0.15240043268490941, -0.22891671690478535, 0.042214351145537531, 0.0063880507928464562, -0.13918415447500068, 0.16583628567907083, 0.17927882137470008, 0.072879058383851864, 0.0022553847965841229, 0.11189501584100021, 0.077450748531926697, 0.019043282847194282, -0.040535127551571529, 0.088883246554846571, 0.043270500438733477, -0.1157020294981792, 0.025654682911779615, 0.10561556615664984, -0.298213839575171, 0.069434682283025559, -0.032568962398945765, -0.010769404085321373, -0.26415984764151496, -0.15009429889594836, 0.038146809503593594, 0.055342502441267309, -0.083379484441784654, 0.10972053643704505, -0.081180448642573289, 0.07057538515719454, -0.061626629007544509, -0.002529445428940633, -0.15459792597108482, 0.11241148019451176, 0.046350254379682695, 0.087372735669356813, 0.29826188859057456, 0.27266501733236159, 0.19553215233676355, -0.1566834736183178, 0.11913037396480364, 0.0048259562812518518, 0.12445844507757264, 0.23209718666245108, -0.22116535794920125, -0.018962232231122811, 0.093336470032812965, 0.010947407367020668, 0.034989264761515398, 0.26599965324906233, 0.030469152345253537, 0.071918548186905185, -0.153603444665979, 0.037588788122951813, -0.024968204407516605, 0.050843652749389269, -0.040657495484533464, -0.11323219345049537, -0.12054603060945444, -0.22520508099012262, 0.0012193233864079495, 0.12328787112095294, 0.011984905816960798, 0.048410945762064353, 0.053035468591094689, -0.093904637193878121
        },  {
            0.15241522274956062, 0.30325481993912889, -0.053670184866991488, 0.15730755885598272, -0.094372624398540286, -0.039639925851096741, -0.035673619694280387, 0.10893637505268873, -0.17898021761010119, 0.25044191285175255, 0.028633221901184482, -0.041882805004697633, 0.037067716268943389, -0.17442507874972749, 0.072151560098275627, -0.24665691014379915, -0.042701759659290746, -0.16400377319373646, 0.14697598433836218, 0.14789827943966638, -0.14790845029878905, -0.069218404903875458, -0.10351260680814511, -0.019960591307556529, 0.041361078414175238, -0.26090574923939719, -0.040875825880499532, 0.089172980587489875, -0.18125108704591039, -0.19083200016332416, -0.1224541302400534, 0.090100376176923444, -0.21863495682946699, 0.11160695160935809, 0.017988088581140677, -0.031831055988964053, 0.043232498929420916, 0.12207995172075709, 0.10555865570075816, -0.00017473987737651333, 0.1652079123841399, -0.086559966507521, -0.029162475669579805, -0.092986290244459605, 0.099404957461722038, -0.022534525007194078, 0.018829381537853847, -0.07056478174755404, -0.037316535318885, 0.0012870389202787218, 0.13080900135275289, -0.093504156203518554, -0.021113359245171968, 0.016606592911329016, -0.039067472231714251, -0.07627616660992817, -0.069928121697846124, 0.16591127173469106, -0.033626177233087202, -0.044163042190245014, -0.20945844202390088, -0.089688092479620007, -0.19935004045586777, -0.035366799602582606, -0.0057670803982141769, 0.014756768078693902, 0.14466888099578398, 0.16945542997859545, -0.098280813167126058
        },  {
            0.065576050910119255, -0.10966697424360111, 0.0054008140860095401, -0.065837234982712337, 0.079561764509639338, 0.025474115406119813, 0.017092001843029583, -0.08266912625031797, 0.055284760000149029, 0.11852831448971511, 0.092566262018282136, 0.037608765831103209, -0.093337800647724789, -0.073078133461673217, -0.015700069674191224, -0.06284536501109772, -0.17346001109465686, -0.061266676036555459, -0.15548320390354423, 0.12323188748212367, -0.0014778512671888267, -0.14128799320509183, -0.076741105078046309, 0.16265904777636433, -0.036174542630150283, 0.073771230620776063, -0.057958870596599855, 0.029435041742520089, 0.058814132087188782, -0.018996778019632902, 0.064048232351164497, 0.30901806139027765, -0.15242691159347718, 0.12165437766427721, -0.057984852977039592, -0.098046776842491065, 0.11575369415455382, -0.077081564458335944, 0.0030306395890463134, 0.052919732914196396, 0.11253447048845562, 0.043959342055596026, 0.071988401966034926, -0.085163828263939645, -0.21019998300243581, -0.028340346812522722, 0.13343998398512266, -0.10658536448220082, -0.019209336522742464, -0.09359952449467028, 0.19567681982262303, 0.024665776713979719, -0.24087100909440332, -0.051060901147382626, 0.10947034238370441, 0.090539085082983806, 0.17585034174407926, 0.041558565810473562, -0.080004876261521252, -0.0032791164009032753, 0.15103521761804137, 0.0077927052361038748, 0.34129853350648209, 0.049777630622365732, -0.16012965887486358, -0.014404321913110698, 0.20852823939564374, -0.037469853449751719, -0.23399945045231269
        },  {
            -0.098500674612828382, -0.092263095981510265, 0.1584112339758546, 0.0047419465550921861, 0.062757983659390032, 0.040196116800026098, 0.097726233684701139, -0.044705242650331205, -0.09467036050562451, -0.12513261688544239, -0.01332900888541569, -0.087619382031914494, -0.21521292286476201, -0.0067271466502288064, 0.033985511271649664, 0.058760676776305761, 0.099672290495539023, -0.060000809453351456, -0.059208322466614838, 0.12859703151741445, -0.051882977492634041, -0.11192562068191558, -0.011408421827807411, -0.078665216784002776, 0.2785683572524334, -0.088578720972456101, 0.19147874453979893, -0.071711292811601199, -0.040629239476210451, -0.057816801667944583, 0.090290996947424554, -0.086341191123586583, 0.17430239078757753, 0.064812996588756444, -0.10979500893103866, -0.050433717280470962, -0.074134951187901713, 0.13886341321165088, 0.078206644428934283, 0.28639594834627868, 0.063867574650198405, -0.10221764269871204, -0.020751552758860831, 0.10529302886448567, -0.064112692321827883, 0.19821271138582616, 0.29129769885992585, 0.088022341563880585, 0.047522235836818347, 0.027052729527877149, 0.057023878891211188, -0.045909347112576167, 0.015306540135907352, 0.011586424087955712, -0.15737029087393872, 0.25813217290134755, 0.1961333834640695, -0.022584580368567937, 0.041571952839007748, 0.23031502074915813, 0.10321457459284432, -0.034710723104772308, -0.070870427906549641, -0.22888645838445881, 0.10478194143630187, 0.0067099029086691939, -0.054498269668404438, -0.089589338054011994, -0.23123887369750232
        },  {
            0.094467932628704371, 0.082616691656872521, -0.19450664907486578, 0.037148316338822526, -0.036143119874813938, 0.0091015131901161506, 0.077315759733411343, -0.097909909860479594, 0.14123458805270223, -0.15902079989315193, 0.22121448860668161, 0.048030926667140254, -0.10031316155487248, -0.055301968828571323, -0.025609186284005384, -0.016653721104032296, -0.035428375854256129, -0.0081048846591093085, -0.021543059627773857, -0.00075375577876646434, 0.12985808261583373, -0.074475172644298426, 0.20914544264494908, 0.0542312077675167, -0.013637011247281432, -0.16936839762135197, 0.11494384497765048, -0.224077437609085, 0.004349642322791337, 0.1778850528397394, -0.0020744050499533523, 0.12157416251802151, -0.033066246286148973, 0.072595976412531599, -0.026938664085762693, -0.01816539774982804, 0.039319234311641693, -0.12795386080183524, 0.042986977474721716, 0.06863479017279514, 0.04962295171872326, -0.17026023011848537, 0.067070222513070349, 0.13645617673343427, -0.062515808376886792, -0.022207781748616942, -0.035182450842042315, -0.099648557772495705, -0.088369845577879122, -0.016607726486668983, 0.23607535884589498, 0.17201689049163155, -0.026350325715339976, 0.0072734636693438581, -0.068255591999899834, -0.38635351983870914, 0.045668196649174937, -0.081126876585906019, 0.0084312825757786453, 0.071607781364891612, -0.036798760520597912, -0.06625199421205541, -0.012392586199351921, -0.057850981958972168, 0.042994224249245358, -0.13000119131146248, -0.011846582825983755, 0.010848192463308974, -0.013527789913426545
        },  {
            0.18387167250334535, -0.047124653196328532, 0.18054487661544605, 0.18420033636645353, -0.10887535997736686, 0.010465753945361251, 0.14525184130990954, 0.41117713855412497, 0.006036584207336583, 0.0077242500005817796, -0.11511666217975233, -0.20597010291889994, -0.070493366054693307, -0.27749602942465618, -0.036520673378650718, -0.19602262648749691, 0.080502356311478396, 0.17018210348975865, -0.011909318791178179, 0.073714636464839098, 0.10614223950336449, 0.045984283449173158, -0.2550583588675655, -0.17274454404130746, -0.11344951382159216, 0.18169385304829228, 0.046552479677370202, -4.490485862687815e-05, -0.071771573913711487, 0.042509575726477025, -0.020011726232860887, 0.18801299443011352, 0.013323149675660968, -0.02100286262055991, 0.0040673286360942834, -0.045986404922160745, 0.26310865131552491, -0.057450174417503025, 0.01429488098924114, 0.096147515390148763, 0.060832971389958343, -0.076298557955738491, -0.032488393283318262, 0.039888221932270179, 0.030079619769521056, -0.10929798238088777, 0.17718746919198627, -0.01751444664487008, -0.15591032222644666, 0.091696200512241047, 0.016082174164588629, -0.092054091444325151, -0.018243122669412911, 0.10738408433631676, -0.020446632088359874, -0.074727896497620017, -0.036048757298404593, 0.048624310705735835, 0.018319280816669167, -0.16839151515328915, 0.22376869495890223, -0.21159852401667992, 0.061922190838095872, 0.11841931479304556, 0.24679230608679523, 0.011835914614213947, -0.0069917573074554512, 0.027077789281967017, -0.14726959499404332
        },  {
            -0.23570296918327249, 0.18417472359570297, -0.042507672092157563, -0.0018989567935733757, -0.0783121416965986, -0.052429870017816453, 0.051404609867885691, -0.046723814477990323, 0.27059234437830887, -0.019829418004982551, 0.13719176490899199, 0.098739500204298669, -0.14794303476439158, -0.048969268311513152, 0.021252713598632067, 0.12905123015712758, 0.18152692017365094, -0.10837744270686636, -0.20013946418109199, 0.076025953449637584, -0.088401801821923517, 0.27750032479113595, -0.21790585716556826, -0.23655622692275891, -0.15562064599660949, -0.042921292619188062, -0.076503681118126957, -0.20436173219992648, 0.026218553287156408, 0.088355890397980974, -0.096375389116521937, 0.065059396487748389, -0.10361478513971292, -0.22038310138755932, -0.20696240694542578, -0.049156939339657701, 0.054007474566250671, -0.099082449141097637, -0.038766086121283128, -0.19617638454791861, 0.0087888228344403987, 0.19925028784020507, 0.16295631499549978, -0.11439201991453446, -0.16948896976490616, -0.092670284995870669, -0.13686991062911785, -0.021381193040955496, -0.12896384619167295, -0.12977781533315433, 0.19066388023369954, 0.29640015374197115, -0.15026581190350308, 0.081482489225361915, 0.021804694427390879, 0.0012703826273169807, 0.098553474689371978, 0.074373875489366706, 0.092047620040988862, -0.050141657552147965, 5.729256627599021e-05, -0.13305433842384168, -0.13770627625837681, 0.11516012086461934, -0.080842486055102081, 0.1900142046037217, -0.27205540472546874, -0.22949835534459798, -0.082542161056631883
        },
    };
    QVector<double> embeddingFrom(const QVector<double>&v) {
        QVector<double> out(16,.5);
        for(int r=0;r<16;++r) {
            double z=0;
            for(int c=0;c<69;++c) {
                double x=c<v.size()?v[c]:NAN;
                double scale=1.0;
                QString n=featureNames[c];
                if(n.startsWith("m_speed_cv"))scale=1.0;
                else if(n.startsWith("m_speed"))scale=1000.0;
                else if(n.startsWith("m_acc"))scale=20000.0;
                else if(n.startsWith("m_jerk"))scale=1e6;
                else if(n.startsWith("m_stroke_len"))scale=300.0;
                else if(n.startsWith("m_stroke_dur"))scale=.3;
                else if(n.startsWith("m_stroke_rate"))scale=2.0;
                else if(n.startsWith("m_dir"))scale=.125;
                else if(n.startsWith("c_hold"))scale=.1;
                else if(n.startsWith("c_int"))scale=.5;
                else if(n.startsWith("k_rate"))scale=2.0;
                else if(n.startsWith("k_hold"))scale=.1;
                else if(n.startsWith("k_flight"))scale=.3;
                else if(n.startsWith("k_unique"))scale=5.0;
                else if(n.startsWith("k_entropy"))scale=2.0;
                else if(n.startsWith("k_freq"))scale=.1;
                double t=std::log1p(std::abs(std::isfinite(x)?x:0.0)/scale);
                double vv=std::isfinite(x)?t/(1.0+t):.5;
                z+=Projection[r][c]*(vv-.5);
            }
            out[r]=.5+.5*std::tanh(6.0*z);
        }
        return out;
    }
    QString idFrom(const QVector<double>&v) {
        int usable=0;
        for(int i=0;i<std::min(69,int(v.size()));++i)if(std::isfinite(v[i]))++usable;
        if(usable<10)return  {
        };
        auto emb=embeddingFrom(v);
        QString out;
        out.reserve(160);
        QVector<double> xs=emb;
        for(int j=0;j<xs.size();++j)xs[j]=std::clamp(xs[j],0.0,1.0-std::ldexp(1.0,-53));
        for(int k=0;k<10;++k)for(int j=0;j<16;++j) {
            xs[j]*=32.0;
            int d=std::clamp(int(xs[j]),0,31);
            xs[j]-=d;
            out+=Alphabet[d];
        }
        return out;
    }
    QString formatId(const QString&raw,int length=64) {
        if(raw.isEmpty())return "(not enough data yet)";
        int n=std::max(1,std::min(length/16,int(raw.size())/16));
        QString s=raw.left(n*16),out="FP-";
        for(int i=0;i<s.size();i+=8) {
            if(i)out+="-";
            out+=s.mid(i,8);
        }
        return out;
    }
    QVector<double> typical(const Profile&p) {
        QVector<double>v(featureNames.size(),NAN);
        int ki=featureNames.indexOf("k_rate"),hi=featureNames.indexOf("k_hold_med");
        for(int j=0;j<v.size();j++) {
            QVector<double>x;
            for(auto w:p.windows)if(j<w.size()&&std::isfinite(w[j])&&(j!=ki||(hi>=0&&std::isfinite(w[hi]))))x<<w[j];
            v[j]=quantile(x,.5);
        }
        return v;
    }
    QVector<double> profileMean(const Profile&p) {
        QVector<double> v(featureNames.size(),NAN);
        for(int j=0;j<v.size();++j) {
            QVector<double>x;
            for(const auto&w:p.windows)if(j<w.size()&&std::isfinite(w[j]))x<<w[j];
            if(x.size()>=3)v[j]=mean(x);
        }
        return v;
    }
    QVector<double> profileSd(const Profile&p) {
        QVector<double> mu=profileMean(p),v(featureNames.size(),NAN);
        for(int j=0;j<v.size();++j) {
            QVector<double>x,dev;
            for(const auto&w:p.windows)if(j<w.size()&&std::isfinite(w[j]))x<<w[j];
            if(x.size()<3)continue;
            double med=quantile(x,.5);
            for(double a:x)dev<<std::abs(a-med);
            double mad=1.4826*quantile(dev,.5);
            double classic=sd(x);
            double z=.5*(std::isfinite(mad)?mad:0.0)+.5*(std::isfinite(classic)?classic:0.0);
            v[j]=std::max({z,std::abs(std::isfinite(mu[j])?mu[j]:0.0)*.1,1e-3});
        }
        return v;
    }
    QVector<double> featureWeights(const QMap<QString,Profile>&profiles) {
        QVector<double>w(featureNames.size(),1.0);
        if(profiles.size()<3)return  {
        };
        QVector<QVector<double>>mus,sds;
        for(auto it=profiles.begin();it!=profiles.end();++it) {
            mus<<profileMean(it.value());
            sds<<profileSd(it.value());
        }
        double avg=0;
        for(int j=0;j<w.size();++j) {
            QVector<double>x,within;
            for(int i=0;i<mus.size();++i) {
                if(std::isfinite(mus[i][j]))x<<mus[i][j];
                if(std::isfinite(sds[i][j]))within<<sds[i][j]*sds[i][j];
            }
            double between=sd(x);
            double b=std::isfinite(between)?between*between:0;
            double wi=mean(within);
            double r=b/std::max(std::isfinite(wi)?wi:0.0,1e-12);
            w[j]=std::clamp(std::isfinite(r)?r:0.0,.05,6.0);
            avg+=w[j];
        }
        avg/=double(std::max(1,int(w.size())));
        for(double&x:w)x=std::clamp(x/std::max(avg,1e-9),.2,3.0);
        return w;
    }
    std::optional<double> distance(const QVector<double>&vec,const QVector<double>&mu,const QVector<double>&sdev,const QVector<double>&weights={}) {
        double sum=0,ws=0;
        int n=0;
        for(int i=0;i<std::min({vec.size(),mu.size(),sdev.size()});++i) {
            if(!std::isfinite(vec[i])||!std::isfinite(mu[i])||!std::isfinite(sdev[i]))continue;
            double z=std::min(4.0,std::abs(vec[i]-mu[i])/std::max(sdev[i],1e-9));
            double w=(i<weights.size()&&std::isfinite(weights[i]))?weights[i]:1.0;
            sum+=z*w;
            ws+=w;
            ++n;
        }
        if(n<10||ws<=0)return  {
        };
        return sum/ws;
    }
    double selfReference(const Profile&p,const QVector<double>&weights={}) {
        if(p.windows.size()<2)return 1.0;
        auto mu=profileMean(p),sdv=profileSd(p);
        QVector<double>d;
        for(const auto&w:p.windows) {
            auto x=distance(w,mu,sdv,weights);
            if(x)d<<*x;
        }
        double r=mean(d);
        return std::max(std::isfinite(r)?r:0.0,0.25);
    }
    double selfSpread(const Profile&p) {
        auto mu=profileMean(p),sdv=profileSd(p);
        QVector<double>d;
        for(const auto&w:p.windows) {
            auto x=distance(w,mu,sdv);
            if(x)d<<*x;
        }
        double v=sd(d);
        return std::isfinite(v)?v:.3;
    }
    std::optional<double> compareProfilesScore(const Profile&cand,const Profile&ref,const QVector<double>&weights={}) {
        auto mu=profileMean(ref),sdv=profileSd(ref);
        double rm=selfReference(ref,weights);
        QVector<double>scores;
        for(const auto&w:cand.windows) {
            auto d=distance(w,mu,sdv,weights);
            if(d)scores<<*d/rm;
        }
        if(scores.size()<3)return  {
        };
        return quantile(scores,.5);
    }
    std::optional<double> pairScore(const Profile&a,const Profile&b,const QVector<double>&weights={}) {
        auto x=compareProfilesScore(a,b,weights),y=compareProfilesScore(b,a,weights);
        if(!x||!y)return  {
        };
        return (*x+*y)/2.0;
    }
    double keySimilarity(const Profile&a,const Profile&b) {
        double ta=0,tb=0;
        for(auto i=a.keyStats.begin();i!=a.keyStats.end();++i)ta+=i.value().value(0);
        for(auto i=b.keyStats.begin();i!=b.keyStats.end();++i)tb+=i.value().value(0);
        if(ta<20||tb<50)return NAN;
        QMap<QString,double>pa,pb;
        for(auto i=a.keyStats.begin();i!=a.keyStats.end();++i)pa[i.key()]=i.value().value(0)/ta;
        for(auto i=b.keyStats.begin();i!=b.keyStats.end();++i)pb[i.key()]=i.value().value(0)/tb;
        QSet<QString>all,ca,cb;
        for(auto i=pa.begin();i!=pa.end();++i) {
            all.insert(i.key());
            if(i.value()>=.04)ca.insert(i.key());
        }
        for(auto i=pb.begin();i!=pb.end();++i) {
            all.insert(i.key());
            if(i.value()>=.04)cb.insert(i.key());
        }
        double bc=0;
        for(const QString&k:all)bc+=std::sqrt(pa.value(k)*pb.value(k));
        QSet<QString>uni=ca;
        uni.unite(cb);
        QSet<QString>inter=ca;
        inter.intersect(cb);
        double jac=uni.isEmpty()?0.0:inter.size()/double(uni.size());
        return std::min(1.0,.6*bc+.4*jac);
    }
    std::optional<double> stability(const Profile&p) {
        int n=p.windows.size();
        if(n<12)return  {
        };
        Profile a;
        a.windows=p.windows.mid(0,n/2);
        auto mu=profileMean(a),sdv=profileSd(a);
        double rm=selfReference(a);
        QVector<double>d;
        for(int i=n/2;i<n;++i) {
            auto x=distance(p.windows[i],mu,sdv);
            if(x)d<<*x;
        }
        if(d.isEmpty())return  {
        };
        return mean(d)/rm;
    }
    QByteArray gzipCompress(QByteArray in) {
        z_stream z {
        };
        if(deflateInit2(&z,Z_BEST_COMPRESSION,Z_DEFLATED,MAX_WBITS+16,8,Z_DEFAULT_STRATEGY)!=Z_OK)return  {
        };
        z.next_in=(Bytef*)in.data();
        z.avail_in=in.size();
        QByteArray out;
        char buf[16384];
        int ret;
        do {
            z.next_out=(Bytef*)buf;
            z.avail_out=sizeof(buf);
            ret=deflate(&z,Z_FINISH);
            out.append(buf,sizeof(buf)-z.avail_out);
        }
        while(ret==Z_OK);
        deflateEnd(&z);
        return ret==Z_STREAM_END?out:QByteArray();
    }
    QByteArray gzipDecompress(QByteArray in) {
        z_stream z {
        };
        if(in.size()<2||inflateInit2(&z,MAX_WBITS+16)!=Z_OK)return  {
        };
        z.next_in=(Bytef*)in.data();
        z.avail_in=in.size();
        QByteArray out;
        char buf[16384];
        int ret;
        do {
            z.next_out=(Bytef*)buf;
            z.avail_out=sizeof(buf);
            ret=inflate(&z,Z_NO_FLUSH);
            if(ret!=Z_OK&&ret!=Z_STREAM_END)break;
            out.append(buf,sizeof(buf)-z.avail_out);
        }
        while(ret!=Z_STREAM_END);
        inflateEnd(&z);
        return ret==Z_STREAM_END?out:QByteArray();
    }
    QJsonObject profileJson(Profile&p) {
        auto v=typical(p);
        p.cls=classCode(v);
        p.id=idFrom(v);
        QJsonArray names,wins;
        QJsonObject keyObject,summary;
        QVector<QPair<QString,QVector<double>>> keyRows;
        for(auto it=p.keyStats.begin();it!=p.keyStats.end();++it)keyRows<<qMakePair(it.key(),it.value());
        std::sort(keyRows.begin(),keyRows.end(),[](const auto&a,const auto&b){return a.second.value(0)>b.second.value(0);});
        if(keyRows.size()>60)keyRows.resize(60);
        for(const auto&kr:keyRows) {
            QJsonArray row;
            for(double x:kr.second)row.append(num(x));
            keyObject.insert(kr.first,row);
        }
        for(auto n:featureNames)names.append(n);
        for(auto w:p.windows) {
            QJsonArray row;
            for(double x:w)row.append(num(x));
            wins.append(row);
        }
        for(int j=0;j<featureNames.size();++j) {
            QVector<double>x;
            for(const auto&w:p.windows)if(j<w.size()&&std::isfinite(w[j]))x<<w[j];
            if(x.size()>=3)summary.insert(featureNames[j],QJsonObject{{"mean",num(mean(x))},{"std",num(sd(x))},{"p10",num(quantile(x,.1))},{"p50",num(quantile(x,.5))},{"p90",num(quantile(x,.9))}});
        }
        double ref=selfReference(p);
        auto st=stability(p);
        QJsonArray emb;
        auto e=embeddingFrom(v);
        int usable=0;
        for(int i=0;i<std::min(69,int(v.size()));++i)if(std::isfinite(v[i]))++usable;
        if(usable>=10)for(double x:e)emb.append(x);
        QJsonObject o {
            {
                "format","play-fingerprint"
            }, {
                "version",2
            }, {
                "name",p.name
            }, {
                "created",p.created
            }, {
                "updated",QDateTime::currentDateTime().toString(Qt::ISODate)
            }, {
                "window_sec",WindowSec
            }, {
                "seconds_recorded",std::round(p.seconds*10.0)/10.0
            }, {
                "feature_names",names
            }, {
                "ref",QJsonObject {
                    {
                        "mean",ref
                    }, {
                        "std",selfSpread(p)
                    }
                }
            }, {
                "class",p.cls
            }, {
                "stability",st?num(*st):QJsonValue(QJsonValue::Null)
            }, {
                "keys",keyObject
            }, {
                "key_slices",p.keySlices
            }, {
                "embedding",usable>=10?QJsonValue(emb):QJsonValue(QJsonValue::Null)
            }, {
                "id",p.id.isEmpty()?QJsonValue(QJsonValue::Null):QJsonValue(p.id)
            }, {
                "summary",summary
            }, {
                "windows",wins
            }
        };
        if(p.windowTags.size()==p.windows.size()) {
            bool any=false;
            QJsonArray tags;
            for(const auto&t:p.windowTags) {
                tags.append(t);
                any|=!t.isEmpty();
            }
            if(any)o.insert("window_tags",tags);
        }
        if(p.windowNums.size()==p.windows.size()) {
            bool any=false;
            QJsonArray nums;
            for(const auto&n:p.windowNums) {
                if(n.isEmpty())nums.append(QJsonValue(QJsonValue::Null));
                else {
                    nums.append(n);
                    any=true;
                }
            }
            if(any)o.insert("window_num",nums);
        }
        return o;
    }
    bool saveProfile(Profile&p,QString path) {
        if(p.windows.size()>MaxWindows) {
            QVector<QVector<double>> n;
            QStringList tags;
            QVector<QJsonObject> nums;
            bool hasTags=p.windowTags.size()==p.windows.size(),hasNums=p.windowNums.size()==p.windows.size();
            for(int i=0;i<MaxWindows;i++) {
                int ix=i*(p.windows.size()-1)/(MaxWindows-1);
                n<<p.windows[ix];
                if(hasTags)tags<<p.windowTags[ix];
                if(hasNums)nums<<p.windowNums[ix];
            }
            p.windows=n;
            if(hasTags)p.windowTags=tags;
            if(hasNums)p.windowNums=nums;
        }
        QByteArray raw=QJsonDocument(profileJson(p)).toJson(QJsonDocument::Compact);
        QByteArray gz=gzipCompress(raw);
        QSaveFile f(path);
        if(!f.open(QIODevice::WriteOnly))return false;
        f.write(gz);
        return f.commit();
    }
    bool loadProfile(QString path,Profile&out) {
        QFile f(path);
        if(!f.open(QIODevice::ReadOnly))return false;
        QByteArray raw=gzipDecompress(f.readAll());
        QJsonParseError e;
        auto doc=QJsonDocument::fromJson(raw,&e);
        if(e.error!=QJsonParseError::NoError||!doc.isObject())return false;
        auto o=doc.object();
        out.name=o.value("name").toString(QFileInfo(path).completeBaseName());
        out.created=o.value("created").toString();
        out.updated=o.value("updated").toString();
        out.seconds=o.value("seconds_recorded").toDouble();
        out.keySlices=o.value("key_slices").toInt();
        QStringList old;
        for(auto n:o.value("feature_names").toArray())old<<n.toString();
        for(auto rowv:o.value("windows").toArray()) {
            auto row=rowv.toArray();
            QVector<double>w(featureNames.size(),NAN);
            for(int i=0;i<featureNames.size();i++) {
                int oi=old.indexOf(featureNames[i]);
                if(oi>=0&&oi<row.size()&&row[oi].isDouble())w[i]=row[oi].toDouble();
            }
            out.windows<<w;
        }
        auto ko=o.value("keys").toObject();
        for(auto it=ko.begin();it!=ko.end();++it) {
            QVector<double> vals;
            for(auto x:it.value().toArray())vals<<x.toDouble();
            out.keyStats[it.key()]=vals;
        }
        for(auto t:o.value("window_tags").toArray())out.windowTags<<t.toString();
        for(auto n:o.value("window_num").toArray())out.windowNums<<n.toObject();
        out.cls=o.value("class").toString();
        out.id=o.value("id").toString();
        return true;
    }
    QStringList profileFiles() {
        QDir().mkpath(ProfileDir);
        return QDir(ProfileDir).entryList({"*.fpr"},QDir::Files,QDir::Name);
    }
}
class MainWindow:public QMainWindow {
    Q_OBJECTpublic:MainWindow() {
        PF::mainWindowObject=this;
        setWindowTitle("Play Fingerprint");
        resize(1120,860);
        setMinimumSize(900,680);
        QDir().mkpath(PF::ProfileDir);
        PF::installHooks();
        auto*central=new QWidget;
        auto*root=new QVBoxLayout(central);
        tabs=new QTabWidget;
        root->addWidget(tabs);
        status=new QLabel("F8 starts/stops the current Train/Detect session. Nothing records while idle.");
        root->addWidget(status);
        setCentralWidget(central);
        buildTrain();
        buildDetect();
        buildScreen();
        buildKeys();
        buildCompare();
        buildResources();
        buildIntegrations();
        buildServer();
        refreshProfiles();
        timer=new QTimer(this);
        connect(timer,&QTimer::timeout,this,&MainWindow::tick);
        timer->start(250);
        connect(this,&MainWindow::startStopRequested,this,&MainWindow::toggleSession);
        connect(this,&MainWindow::afkRequested,this,&MainWindow::toggleAfk);
    }
    ~MainWindow() {
        PF::recording=false;
        stopIntegrations();
        PF::mainWindowObject=nullptr;
        PF::removeHooks();
    }
    Q_INVOKABLE void hotkeyToggle() {
        emit startStopRequested();
    }
    Q_INVOKABLE void afkToggle() {
        emit afkRequested();
    }
    signals:void startStopRequested();
    void afkRequested();
    private: QTabWidget*tabs {
    };
    QLabel*status {
    };
    QTimer*timer {
    };
    QComboBox*trainProfile {
    };
    QListWidget*detectProfiles {
    };
    QLabel*trainInfo {
    };
    QLabel*detectInfo {
    };
    QTableWidget*compareTable {
    };
    QTableWidget*keysTable {
    };
    QTableWidget*screenTable {
    };
    QTableWidget*banTable {
    };
    QTableWidget*integrationTable {
    };
    QLabel*resourceInfo {
    };
    QLabel*integrationInfo {
    };
    QCheckBox*useContextTags {
    };
    QLineEdit*serverUrl {
    };
    QLineEdit*serverKey {
    };
    QCheckBox*serverWake {
    };
    QCheckBox*serverAutoUpload {
    };
    QLabel*serverInfo {
    };
    QJsonObject integrationCfg;
    QMap<QString,QJsonObject> integrationDefs;
    QMap<QString,QJsonObject> mapperDefs;
    QMap<QString,QVariantMap> integrationValues;
    QMap<QString,qint64> integrationLast;
    QMap<QString,qint64> udpRateEpoch;
    QMap<QString,int> udpRateCount;
    QMap<QString,QTcpServer*> pushServers;
    QMap<QString,QUdpSocket*> udpSockets;
    QMap<QString,QTimer*> integrationTimers;
    QMap<QString,QFile*> tailFiles;
    QMap<QString,qint64> tailPositions;
    QMap<QString,QByteArray> tailBuffers;
    QNetworkAccessManager*net {
    };
    QNetworkAccessManager*localNet {
    };
    int wakeAttempts=0;
    QCheckBox*trackKeys {
    };
    QCheckBox*autoAfk {
    };
    QCheckBox*afkManual {
    };
    QSlider*strictness {
    };
    QComboBox*compareA {
    };
    QComboBox*compareB {
    };
    QElapsedTimer sessionClock;
    double sessionStartSec=0;
    QString mode;
    QString currentProfile;
    PF::Profile profile;
    double nextTrain=20,nextDetect=10;
    int made=0,skipped=0;
    bool manualAfk=false;
    QMap<QString,PF::Profile> profiles;
    QMap<QString,double> ema;
    QVector<QPair<double,QString>> contextTagSamples;
    QWidget*page(QString name) {
        auto*w=new QWidget;
        tabs->addTab(w,name);
        return w;
    }
    QPushButton*button(QString text,QWidget*p,std::function<void()>fn) {
        auto*b=new QPushButton(text,p);
        connect(b,&QPushButton::clicked,this,[fn]{fn();});
        return b;
    }
    void buildTrain() {
        auto*w=page("Train");
        auto*l=new QVBoxLayout(w);
        l->addWidget(new QLabel("Train a fingerprint from normal keyboard and mouse play. Recommended: 10+ minutes."));
        auto*r=new QHBoxLayout;
        trainProfile=new QComboBox;
        trainProfile->setEditable(true);
        r->addWidget(new QLabel("Fingerprint:"));
        r->addWidget(trainProfile,1);
        r->addWidget(button("New",w,[this]{trainProfile->setEditText("Player "+QDateTime::currentDateTime().toString("yyyyMMdd-HHmm"));}));
        l->addLayout(r);
        trackKeys=new QCheckBox("Track individual key totals (key names and aggregate hold times only)");
        trackKeys->setChecked(true);
        l->addWidget(trackKeys);
        autoAfk=new QCheckBox("Auto-detect AFK periods");
        autoAfk->setChecked(true);
        l->addWidget(autoAfk);
        afkManual=new QCheckBox("I am AFK (Num Lock toggles this while training)");
        connect(afkManual,&QCheckBox::toggled,this,[this](bool v){manualAfk=v;});
        l->addWidget(afkManual);
        auto*btns=new QHBoxLayout;
        btns->addWidget(button("Start / Stop (F8)",w,[this]{toggleSession();}));
        btns->addWidget(button("Refresh profiles",w,[this]{refreshProfiles();}));
        l->addLayout(btns);
        trainInfo=new QLabel("Ready. Start recording only when you're in a game.");
        trainInfo->setWordWrap(true);
        l->addWidget(trainInfo);
        l->addStretch();
    }
    void buildDetect() {
        auto*w=page("Detect");
        auto*l=new QVBoxLayout(w);
        l->addWidget(new QLabel("Select one or more fingerprints. Matching is a similarity lead, not proof of identity."));
        detectProfiles=new QListWidget;
        detectProfiles->setSelectionMode(QAbstractItemView::MultiSelection);
        l->addWidget(detectProfiles);
        strictness=new QSlider(Qt::Horizontal);
        strictness->setRange(12,30);
        strictness->setValue(18);
        l->addWidget(new QLabel("Strictness (higher is more lenient)"));
        l->addWidget(strictness);
        useContextTags=new QCheckBox("Compare only against windows with the current context tag (needs 10+ matching windows)");
        l->addWidget(useContextTags);
        l->addWidget(button("Start / Stop (F8)",w,[this]{toggleSession();}));
        detectInfo=new QLabel("Not running");
        detectInfo->setWordWrap(true);
        l->addWidget(detectInfo);
        l->addStretch();
    }
    void buildScreen() {
        auto*w=page("Screen (anti-cheat)");
        auto*l=new QVBoxLayout(w);
        l->addWidget(new QLabel("Ban-evasion screening. Similarity is a lead for human review, never proof of identity or an automatic ban."));
        banTable=new QTableWidget(0,3);
        banTable->setHorizontalHeaderLabels({"Local account","Ban status","Reason"});
        banTable->setSelectionBehavior(QAbstractItemView::SelectRows);
        banTable->setSelectionMode(QAbstractItemView::ExtendedSelection);
        banTable->horizontalHeader()->setStretchLastSection(true);
        l->addWidget(banTable);
        auto*br=new QHBoxLayout;
        br->addWidget(button("Ban selected account(s)...",w,[this]{banSelected();}));
        br->addWidget(button("Unban selected account(s)",w,[this]{unbanSelected();}));
        br->addWidget(button("Refresh",w,[this]{refreshProfiles();populateScreen();}));
        br->addWidget(button("Export screening report...",w,[this]{exportScreenReport();}));
        l->addLayout(br);
        screenTable=new QTableWidget(0,9);
        screenTable->setHorizontalHeaderLabels({"Clean account","Class","Closest banned profile","Distance","Similarity","Population rank","Key similarity","Flag","Reason"});
        screenTable->horizontalHeader()->setStretchLastSection(true);
        l->addWidget(screenTable);
    }
    void buildKeys() {
        auto*w=page("Keys");
        auto*l=new QVBoxLayout(w);
        l->addWidget(new QLabel("Aggregated keybind profile. Typed text and key sequences are never saved."));
        keysTable=new QTableWidget(0,4);
        keysTable->setHorizontalHeaderLabels({"Key","Presses","Share","Typical hold (s)"});
        keysTable->horizontalHeader()->setStretchLastSection(true);
        l->addWidget(keysTable);
        l->addWidget(button("Refresh",w,[this]{populateKeys();}));
    }
    void buildCompare() {
        auto*w=page("Compare");
        auto*l=new QVBoxLayout(w);
        auto*r=new QHBoxLayout;
        compareA=new QComboBox;
        compareB=new QComboBox;
        r->addWidget(new QLabel("Account A"));
        r->addWidget(compareA,1);
        r->addWidget(new QLabel("Account B"));
        r->addWidget(compareB,1);
        r->addWidget(button("Compare",w,[this]{compareProfiles();}));
        l->addLayout(r);
        compareTable=new QTableWidget(0,3);
        compareTable->setHorizontalHeaderLabels({"Statistic","Account A","Account B"});
        compareTable->horizontalHeader()->setStretchLastSection(true);
        l->addWidget(compareTable);
    }
    void buildResources() {
        auto*w=page("Resources");
        auto*l=new QVBoxLayout(w);
        resourceInfo=new QLabel("Process resource use is measured by Windows process counters.");
        resourceInfo->setWordWrap(true);
        l->addWidget(resourceInfo);
        l->addStretch();
    }
    void buildIntegrations() {
        auto*w=page("Integrations");
        auto*l=new QVBoxLayout(w);
        l->addWidget(new QLabel("Opt-in game telemetry only: documented APIs, official state output, or user-owned logs. Listeners bind to loopback. No process memory, injection, or screen scraping."));
        integrationInfo=new QLabel;
        integrationInfo->setWordWrap(true);
        l->addWidget(integrationInfo);
        auto*r=new QHBoxLayout;
        r->addWidget(button("Reload integrations.json",w,[this]{loadIntegrationConfig();}));
        r->addWidget(button("Start enabled",w,[this]{startIntegrations();}));
        r->addWidget(button("Stop all",w,[this]{stopIntegrations();}));
        r->addWidget(button("Open data folder",w,[this]{QDesktopServices::openUrl(QUrl::fromLocalFile(PF::AppDir));}));
        l->addLayout(r);
        integrationTable=new QTableWidget(0,5);
        integrationTable->setHorizontalHeaderLabels({"Name","Kind","Enabled","Status","Details / latest values"});
        integrationTable->horizontalHeader()->setStretchLastSection(true);
        l->addWidget(integrationTable);
        loadIntegrationConfig();
    }
    void refreshProfiles() {
        profiles.clear();
        trainProfile->clear();
        detectProfiles->clear();
        compareA->clear();
        compareB->clear();
        for(auto fn:PF::profileFiles()) {
            PF::Profile p;
            if(PF::loadProfile(PF::ProfileDir+"/"+fn,p)) {
                profiles[p.name]=p;
                trainProfile->addItem(p.name);
                auto*i=new QListWidgetItem(p.name,detectProfiles);
                i->setFlags(i->flags()|Qt::ItemIsUserCheckable);
                i->setCheckState(Qt::Unchecked);
                compareA->addItem(p.name);
                compareB->addItem(p.name);
            }
        }
        populateKeys();
        populateScreen();
    }
    void toggleSession() {
        if(mode.isEmpty()) {
            if(tabs->currentIndex()==0) {
                QString name=trainProfile->currentText().trimmed();
                if(name.isEmpty()) {
                    QMessageBox::warning(this,"Name required","Enter a fingerprint name first.");
                    return;
                }
                currentProfile=name;
                profile=profiles.value(name,PF::Profile{});
                if(profile.name.isEmpty()) {
                    profile.name=name;
                    profile.created=QDateTime::currentDateTime().toString(Qt::ISODate);
                }
                mode="train";
                nextTrain=PF::TrainWarmup;
                made=skipped=0;
                sessionClock.start();
                sessionStartSec=PF::nowSec();
                PF::recording=true;
                {
                    std::lock_guard<std::mutex>g(PF::dataMutex);
                    PF::moves.clear();
                    PF::buttons.clear();
                    PF::scrolls.clear();
                    PF::keys.clear();
                    PF::keyDown.clear();
                    PF::keyHolds.clear();
                    PF::keyPresses.clear();
                }
                trainInfo->setText("RECORDING — warm-up for 20 seconds, then 15-second windows are analysed every 7.5 seconds.");
            }
            else if(tabs->currentIndex()==1) {
                QStringList selected;
                for(int i=0;i<detectProfiles->count();i++) {
                    auto*it=detectProfiles->item(i);
                    if(it->checkState()==Qt::Checked)selected<<it->text();
                }
                if(selected.isEmpty()) {
                    QMessageBox::information(this,"Select fingerprints","Check one or more profiles in Detect first.");
                    return;
                }
                mode="detect";
                ema.clear();
                sessionClock.start();
                sessionStartSec=PF::nowSec();
                nextDetect=PF::DetectWarmup;
                PF::recording=true;
                {
                    std::lock_guard<std::mutex>g(PF::dataMutex);
                    PF::moves.clear();
                    PF::buttons.clear();
                    PF::scrolls.clear();
                    PF::keys.clear();
                    PF::keyDown.clear();
                    PF::keyHolds.clear();
                    PF::keyPresses.clear();
                }
                detectInfo->setText("RECORDING — warming up for 10 seconds.");
            }
            else {
                QMessageBox::information(this,"Start a session","Select Train or Detect before pressing F8.");
                return;
            }
            status->setText("● RECORDING — F8 stops the session");
        }
        else stopSession();
    }
    void stopSession() {
        PF::recording=false;
        if(mode=="train") {
            profile.seconds+=sessionClock.elapsed()/1000.0;
            profile.updated=QDateTime::currentDateTime().toString(Qt::ISODate);
            if(trackKeys->isChecked()) {
                std::lock_guard<std::mutex>g(PF::dataMutex);
                for(auto it=PF::keyPresses.begin();it!=PF::keyPresses.end();++it) {
                    auto&row=profile.keyStats[it->first];
                    while(row.size()<5)row<<0.0;
                    row[0]+=it->second;
                    auto hs=PF::keyHolds[it->first];
                    for(double h:hs) {
                        row[1]+=1;
                        row[2]+=h;
                        row[3]+=h*h;
                    }
                    row[4]+=1;
                }
                profile.keySlices++;
            }
            QString path=PF::ProfileDir+"/"+QFileInfo(currentProfile).fileName()+".fpr";
            if(PF::saveProfile(profile,path)) {
                profiles[currentProfile]=profile;
                trainInfo->setText(QString("Saved %1 windows (%2 skipped) to %3").arg(profile.windows.size()).arg(skipped).arg(path));
            }
            else trainInfo->setText("Could not save fingerprint. Check folder permissions.");
            refreshProfiles();
            if(serverAutoUpload&&serverAutoUpload->isChecked()&&!serverUrl->text().trimmed().isEmpty()) {
                saveServerConfig();
                uploadQueue= {
                    QFileInfo(currentProfile).fileName()+".fpr"
                };
                wakeAttempts=0;
                serverRequest("GET","/health",{},false,"upload-ready");
            }
        }
        else if(mode=="detect")detectInfo->setText("Stopped.");
        mode.clear();
        status->setText("Idle — nothing is being recorded");
    }
    void tick() {
        double tagNow=PF::nowSec();
        PF::pruneEvents(tagNow);
        contextTagSamples<<qMakePair(tagNow,currentContextTag());
        while(!contextTagSamples.isEmpty()&&contextTagSamples.first().first<tagNow-PF::WindowSec)contextTagSamples.removeFirst();
        refreshIntegrationTable();
        if(mode.isEmpty()) {
            PROCESS_MEMORY_COUNTERS_EX pm {
            };
            pm.cb=sizeof(pm);
            HANDLE h=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|PROCESS_VM_READ,FALSE,GetCurrentProcessId());
            SIZE_T ws=0;
            if(h) {
                if(GetProcessMemoryInfo(h,(PROCESS_MEMORY_COUNTERS*)&pm,sizeof(pm)))ws=pm.WorkingSetSize;
                CloseHandle(h);
            }
            resourceInfo->setText(QString("Play Fingerprint C++ process\nWorking set: %1 MB\nProfile folder: %2").arg(ws/1024.0/1024.0,0,'f',1).arg(PF::ProfileDir));
            return;
        }
        double t=sessionClock.elapsed()/1000.0;
        if(mode=="train"&&t>=nextTrain) {
            bool afk=manualAfk||integrationAfk();
            if(!afk&&autoAfk->isChecked()) {
                std::lock_guard<std::mutex>g(PF::dataMutex);
                double newest=-1;
                for(const auto&x:PF::moves)newest=std::max(newest,x.t);
                for(const auto&x:PF::buttons)newest=std::max(newest,x.t);
                for(const auto&x:PF::keys)newest=std::max(newest,x.t);
                afk=(newest<0||PF::nowSec()-newest>5.0);
            }
            if(afk) {
                skipped++;
                trainInfo->setText(QString("AFK/idle window skipped — %1 skipped; %2 seconds elapsed").arg(skipped).arg(int(t)));
                nextTrain+=PF::TrainStep;
                return;
            }
            auto v=PF::extract(sessionStartSec+t-PF::WindowSec,sessionStartSec+t);
            int valid=0;
            for(double x:v)if(std::isfinite(x))valid++;
            if(valid>=10) {
                while(profile.windowTags.size()<profile.windows.size())profile.windowTags<<QString();
                while(profile.windowNums.size()<profile.windows.size())profile.windowNums<<QJsonObject();
                profile.windows<<v;
                profile.windowTags<<dominantContextTag();
                profile.windowNums<<currentContextNums();
                made++;
                trainInfo->setText(QString("RECORDING — %1 analysed windows; %2 skipped; %3 seconds elapsed").arg(made).arg(skipped).arg(int(t)));
            }
            else skipped++;
            nextTrain+=PF::TrainStep;
        }
        else if(mode=="detect"&&t>=nextDetect) {
            auto live=PF::extract(sessionStartSec+t-PF::WindowSec,sessionStartSec+t);
            QVector<QPair<double,QString>> scores;
            auto weights=PF::featureWeights(profiles);
            QString liveTag=(useContextTags&&useContextTags->isChecked())?dominantContextTag():QString();
            int tagUsed=0;
            for(auto it=profiles.begin();it!=profiles.end();++it) {
                bool checked=false;
                for(int i=0;i<detectProfiles->count();i++)if(detectProfiles->item(i)->text()==it.key()&&detectProfiles->item(i)->checkState()==Qt::Checked)checked=true;
                if(!checked)continue;
                PF::Profile refProfile=it.value();
                bool usedTag=false;
                if(!liveTag.isEmpty()&&refProfile.windowTags.size()==refProfile.windows.size()) {
                    QVector<QVector<double>> tagged;
                    for(int j=0;j<refProfile.windows.size();++j)if(refProfile.windowTags[j]==liveTag)tagged<<refProfile.windows[j];
                    if(tagged.size()>=10) {
                        refProfile.windows=tagged;
                        usedTag=true;
                        tagUsed++;
                    }
                }
                auto ref=PF::profileMean(refProfile),sdv=PF::profileSd(refProfile);
                auto d=PF::distance(live,ref,sdv,weights);
                if(d) {
                    double score=*d/PF::selfReference(refProfile,weights);
                    double prev=ema.value(it.key(),NAN);
                    ema[it.key()]=std::isfinite(prev)?.6*prev+.4*score:score;
                    scores<<qMakePair(ema[it.key()],it.key());
                }
            }
            std::sort(scores.begin(),scores.end(),[](auto a,auto b){return a.first<b.first;});
            QString out="Live class: "+PF::classCode(live)+"\nLive ID: "+PF::formatId(PF::idFrom(live))+"\n";
            if(scores.isEmpty())out+="Not enough usable data or no profiles selected.";
            else {
                double thr=strictness->value()/10.0;
                QSet<QString>banned;
                QFile bf(PF::ProfileDir+"/banlist.json");
                if(bf.open(QIODevice::ReadOnly)) {
                    auto bd=QJsonDocument::fromJson(bf.readAll());
                    if(bd.isObject())for(auto k:bd.object().keys())banned.insert(k);
                }
                QString best=scores.first().second;
                double bestScore=scores.first().first;
                if(bestScore<=thr&&banned.contains(best))out+="[REVIEW] Closest selected profile is on the local ban list: "+best+"\n";
                else if(bestScore<=thr)out+="Closest selected profile: "+best+" (review lead only)\n";
                else out+="No selected profile is within the threshold.\n";
                for(int i=0;i<std::min(5,int(scores.size()));i++)out+=QString("%1. %2 — distance %3%4\n").arg(i+1).arg(scores[i].second).arg(scores[i].first,0,'f',2).arg(scores[i].first<=thr?" (within threshold)":"");
                out+=QString("Threshold: %1. Lower distance is more similar; never auto-ban from this score.").arg(thr,0,'f',1);
                if(!liveTag.isEmpty())out+=QString("\nContext tag %1: used for %2/%3 selected profile(s).").arg(liveTag).arg(tagUsed).arg(scores.size());
            }
            for(const auto&flag:PF::automationFlags(live))out+="\nPossible automation signal (not proof): "+flag;
            detectInfo->setText(out);
            nextDetect+=PF::DetectStep;
        }
    }
    void populateKeys() {
        if(!keysTable)return;
        keysTable->setRowCount(0);
        QMap<QString,int>counts;
        QMap<QString,QVector<double>>holds;
        int total=0;
        {
            std::lock_guard<std::mutex>g(PF::dataMutex);
            for(const auto&kv:PF::keyPresses)counts.insert(kv.first,kv.second);
            for(const auto&kv:PF::keyHolds) {
                QVector<double> values;
                for(double x:kv.second)values.append(x);
                holds.insert(kv.first,values);
            }
        }
        for(auto i=counts.begin();i!=counts.end();++i)total+=i.value();
        for(auto i=counts.begin();i!=counts.end();++i) {
            int row=keysTable->rowCount();
            keysTable->insertRow(row);
            keysTable->setItem(row,0,new QTableWidgetItem(i.key()));
            keysTable->setItem(row,1,new QTableWidgetItem(QString::number(i.value())));
            keysTable->setItem(row,2,new QTableWidgetItem(QString("%1%").arg(total?100.0*i.value()/total:0,0,'f',1)));
            keysTable->setItem(row,3,new QTableWidgetItem(QString::number(PF::mean(holds[i.key()]),'f',3)));
        }
    }
    QStringList selectedBanAccounts() {
        QStringList out;
        if(!banTable)return out;
        for(const auto&idx:banTable->selectionModel()->selectedRows())if(auto*item=banTable->item(idx.row(),0))out<<item->text();
        return out;
    }
    void syncBanEntry(QString name,QJsonObject entry,bool remove) {
        if(!serverUrl||!serverKey||serverUrl->text().trimmed().isEmpty()||serverKey->text().trimmed().isEmpty())return;
        QString path="/api/banlist/"+QString::fromLatin1(QUrl::toPercentEncoding(name));
        serverRequest(remove?"DELETE":"PUT",path,remove?QByteArray():QJsonDocument(entry).toJson(QJsonDocument::Compact),true);
    }
    QJsonObject readBanlist() {
        QFile f(PF::ProfileDir+"/banlist.json");
        if(!f.open(QIODevice::ReadOnly))return  {
        };
        auto d=QJsonDocument::fromJson(f.readAll());
        return d.isObject()?d.object():QJsonObject {
        };
    }
    bool saveBanlist(const QJsonObject&o) {
        QDir().mkpath(PF::ProfileDir);
        QSaveFile f(PF::ProfileDir+"/banlist.json");
        if(!f.open(QIODevice::WriteOnly))return false;
        f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
        return f.commit();
    }
    void banSelected() {
        auto names=selectedBanAccounts();
        if(names.isEmpty()) {
            QMessageBox::information(this,"Select accounts","Select one or more rows in the local account table first.");
            return;
        }
        bool ok=false;
        QString reason=QInputDialog::getText(this,"Ban selected accounts","Reason (manual decision; do not rely on the fingerprint score alone):",QLineEdit::Normal,"Manual review",&ok);
        if(!ok)return;
        auto b=readBanlist();
        for(const auto&n:names)b.insert(n,QJsonObject{{"reason",reason},{"date",QDateTime::currentDateTime().toString(Qt::ISODate)}});
        if(!saveBanlist(b)) {
            QMessageBox::warning(this,"Save failed","Could not write fingerprints/banlist.json.");
            return;
        }
        for(const auto&n:names)syncBanEntry(n,b.value(n).toObject(),false);
        populateScreen();
    }
    void unbanSelected() {
        auto names=selectedBanAccounts();
        if(names.isEmpty()) {
            QMessageBox::information(this,"Select accounts","Select one or more rows in the local account table first.");
            return;
        }
        auto b=readBanlist();
        for(const auto&n:names)b.remove(n);
        if(!saveBanlist(b)) {
            QMessageBox::warning(this,"Save failed","Could not write fingerprints/banlist.json.");
            return;
        }
        for(const auto&n:names)syncBanEntry(n,{},true);
        populateScreen();
    }
    void populateScreen() {
        if(!screenTable)return;
        screenTable->setRowCount(0);
        auto weights=PF::featureWeights(profiles);
        QSet<QString>bannedNames;
        bool hasBanlist=false;
        QFile banFile(PF::ProfileDir+"/banlist.json");
        if(banFile.open(QIODevice::ReadOnly)) {
            auto bd=QJsonDocument::fromJson(banFile.readAll());
            if(bd.isObject()) {
                hasBanlist=true;
                for(auto k:bd.object().keys())bannedNames.insert(k);
            }
        }
        if(banTable) {
            banTable->setRowCount(0);
            auto banObj=readBanlist();
            for(auto pi=profiles.begin();pi!=profiles.end();++pi) {
                int br=banTable->rowCount();
                banTable->insertRow(br);
                banTable->setItem(br,0,new QTableWidgetItem(pi.key()));
                bool isB=bannedNames.contains(pi.key());
                banTable->setItem(br,1,new QTableWidgetItem(isB?"BANNED":"Clean"));
                QString why=banObj.value(pi.key()).toObject().value("reason").toString();
                banTable->setItem(br,2,new QTableWidgetItem(why));
            }
        }
        double thr=strictness?strictness->value()/10.0:1.8;
        for(auto it=profiles.begin();it!=profiles.end();++it) {
            if(hasBanlist&&bannedNames.contains(it.key()))continue;
            QString closest="—",reason="";
            double best=1e9,ks=NAN,pct=NAN,sim=NAN;
            QVector<double>population;
            for(auto jt=profiles.begin();jt!=profiles.end();++jt) {
                if(jt.key()==it.key()||bannedNames.contains(jt.key()))continue;
                auto score=PF::pairScore(it.value(),jt.value(),weights);
                if(score)population<<*score;
            }
            if(hasBanlist) {
                for(const QString&bn:bannedNames) {
                    if(bn==it.key()||!profiles.contains(bn))continue;
                    auto score=PF::pairScore(it.value(),profiles[bn],weights);
                    if(score&&*score<best) {
                        best=*score;
                        closest=bn;
                        ks=PF::keySimilarity(it.value(),profiles[bn]);
                    }
                }
                if(best<1e8) {
                    if(population.size()>=5) {
                        int worse=0;
                        for(double x:population)if(x>best)++worse;
                        pct=100.0*worse/population.size();
                    }
                    sim=100.0/(1.0+std::pow(best/thr,4.0));
                    reason=best<=thr?"Similar enough for human review":"Outside review threshold";
                    if(best<=.8*thr&&(!std::isfinite(pct)||pct>=95.0))reason="Very close match; verify manually";
                }
                else reason=bannedNames.isEmpty()?"Ban list is empty":"No banned profile is available locally";
            }
            else reason="No local ban list. Pull one from the server or add fingerprints/banlist.json.";
            QString flag="NONE";
            if(hasBanlist&&best<=thr) {
                flag="REVIEW";
                if(best<=.8*thr&&(!std::isfinite(pct)||pct>=95.0))flag="HIGH";
            }
            int r=screenTable->rowCount();
            screenTable->insertRow(r);
            screenTable->setItem(r,0,new QTableWidgetItem(it.key()));
            screenTable->setItem(r,1,new QTableWidgetItem(it->cls.isEmpty()?PF::classCode(PF::typical(it.value())):it->cls));
            screenTable->setItem(r,2,new QTableWidgetItem(closest));
            screenTable->setItem(r,3,new QTableWidgetItem(best<1e8?QString::number(best,'f',3):"—"));
            screenTable->setItem(r,4,new QTableWidgetItem(std::isfinite(sim)?QString::number(sim,'f',1)+"%":"—"));
            screenTable->setItem(r,5,new QTableWidgetItem(std::isfinite(pct)?QString::number(pct,'f',1)+"%":"—"));
            screenTable->setItem(r,6,new QTableWidgetItem(std::isfinite(ks)?QString::number(ks*100,'f',1)+"%":"—"));
            screenTable->setItem(r,7,new QTableWidgetItem(flag));
            screenTable->setItem(r,8,new QTableWidgetItem(reason));
        }
    }
    void exportScreenReport() {
        QString path=QFileDialog::getSaveFileName(this,"Export screening report",QDir::homePath()+"/screening_report.json","JSON files (*.json)");
        if(path.isEmpty())return;
        QJsonArray rows;
        for(int r=0;r<screenTable->rowCount();++r) {
            auto val=[&](int c) {
                return screenTable->item(r,c)?screenTable->item(r,c)->text():QString();
            };
            QJsonObject o {
                {
                    "candidate",val(0)
                }, {
                    "class",val(1)
                }, {
                    "banned",val(2)
                }, {
                    "flag",val(7)
                }, {
                    "reason",val(8)
                }
            };
            bool ok=false;
            double d=val(3).toDouble(&ok);
            if(ok)o.insert("distance",d);
            double sim=val(4).remove('%').toDouble(&ok);
            if(ok)o.insert("similarity",sim);
            double pct=val(5).remove('%').toDouble(&ok);
            if(ok)o.insert("population_rank",pct);
            double ks=val(6).remove('%').toDouble(&ok);
            if(ok)o.insert("key_similarity",ks/100.0);
            rows.append(o);
        }
        QJsonObject report {
            {
                "generated",QDateTime::currentDateTime().toString(Qt::ISODate)
            }, {
                "threshold",strictness->value()/10.0
            }, {
                "note","Fingerprint similarity is a lead for human review, not proof of identity. Never auto-ban based on this score."
            }, {
                "results",rows
            }
        };
        QSaveFile f(path);
        if(!f.open(QIODevice::WriteOnly)||f.write(QJsonDocument(report).toJson(QJsonDocument::Indented))<0||!f.commit()) {
            QMessageBox::warning(this,"Export failed","Could not write the report.");
            return;
        }
        QMessageBox::information(this,"Exported","Screening report saved to:\n"+path);
    }
    void compareProfiles() {
        auto a=profiles.value(compareA->currentText()),b=profiles.value(compareB->currentText());
        auto av=PF::typical(a),bv=PF::typical(b);
        QStringList names= {
            "Class code","Fingerprint ID","Recorded seconds","Training windows","Keyboard rate (press/s)","Mouse speed mean (px/s)","Click rate (click/s)","Scroll rate (events/s)","Median key hold (s)","Median click interval (s)"
        };
        QVector<int>ix= {
            -1,-2,-3,-4,PF::featureNames.indexOf("k_rate"),PF::featureNames.indexOf("m_speed_mean"),PF::featureNames.indexOf("c_rate"),PF::featureNames.indexOf("s_rate"),PF::featureNames.indexOf("k_hold_med"),PF::featureNames.indexOf("c_int_med")
        };
        compareTable->setRowCount(names.size());
        for(int i=0;i<names.size();i++) {
            compareTable->setItem(i,0,new QTableWidgetItem(names[i]));
            auto val=[&](PF::Profile&p,QVector<double>&v) {
                if(ix[i]==-1)return PF::classCode(v);
                if(ix[i]==-2)return PF::formatId(PF::idFrom(v));
                if(ix[i]==-3)return QString::number(p.seconds,'f',1);
                if(ix[i]==-4)return QString::number(p.windows.size());
                return std::isfinite(v[ix[i]])?QString::number(v[ix[i]],'f',3):QString("—");
            };
            compareTable->setItem(i,1,new QTableWidgetItem(val(a,av)));
            compareTable->setItem(i,2,new QTableWidgetItem(val(b,bv)));
        }
    }
    void buildServer() {
        auto*w=page("Server sync");
        auto*l=new QVBoxLayout(w);
        l->addWidget(new QLabel("Optional shared server. API keys are stored in your local app-data folder. Only HTTPS or loopback HTTP is allowed; do not share your API key."));
        auto*form=new QFormLayout;
        serverUrl=new QLineEdit;
        serverKey=new QLineEdit;
        serverKey->setEchoMode(QLineEdit::Password);
        serverWake=new QCheckBox("Wake server on app start");
        serverWake->setChecked(true);
        serverAutoUpload=new QCheckBox("Upload after training");
        form->addRow("Server URL",serverUrl);
        form->addRow("API key",serverKey);
        form->addRow("",serverWake);
        form->addRow("",serverAutoUpload);
        l->addLayout(form);
        auto*r=new QHBoxLayout;
        r->addWidget(button("Save settings",w,[this]{saveServerConfig();}));
        r->addWidget(button("Test connection",w,[this]{saveServerConfig();serverRequest("GET","/api/status",{},true);}));
        r->addWidget(button("Upload profiles",w,[this]{saveServerConfig();uploadProfiles();}));
        r->addWidget(button("Download profiles",w,[this]{saveServerConfig();downloadProfiles();}));
        l->addLayout(r);
        auto*r2=new QHBoxLayout;
        r2->addWidget(button("Pull ban list",w,[this]{saveServerConfig();serverRequest("GET","/api/banlist",{},true,"banlist");}));
        r2->addWidget(button("Push local ban list",w,[this]{saveServerConfig();pushBanList();}));
        r2->addWidget(button("Run server analysis",w,[this]{saveServerConfig();serverRequest("POST","/api/analysis","{}",true,"analysis");}));
        l->addLayout(r2);
        serverInfo=new QLabel("Server sync is optional.");
        serverInfo->setWordWrap(true);
        l->addWidget(serverInfo);
        l->addStretch();
        loadServerConfig();
        QTimer::singleShot(1200,this,[this]{if(serverWake&&serverWake->isChecked()&&!serverUrl->text().trimmed().isEmpty()){wakeAttempts=0;serverRequest("GET","/health",{},false);}});
    }
    QJsonObject serverConfig() {
        return QJsonObject {
            {
                "url",serverUrl->text().trimmed().remove(QRegularExpression("/+$"))
            }, {
                "key",serverKey->text().trimmed()
            }, {
                "wake_on_start",serverWake->isChecked()
            }, {
                "auto_upload",serverAutoUpload->isChecked()
            }
        };
    }
    void loadServerConfig() {
        QDir().mkpath(PF::AppDir);
        QFile f(PF::AppDir+"/server_config.json");
        if(f.open(QIODevice::ReadOnly)) {
            auto d=QJsonDocument::fromJson(f.readAll());
            if(d.isObject()) {
                auto o=d.object();
                serverUrl->setText(o.value("url").toString());
                serverKey->setText(o.value("key").toString());
                serverWake->setChecked(o.value("wake_on_start").toBool(true));
                serverAutoUpload->setChecked(o.value("auto_upload").toBool(false));
            }
        }
    }
    void saveServerConfig() {
        QDir().mkpath(PF::AppDir);
        QSaveFile f(PF::AppDir+"/server_config.json");
        if(!f.open(QIODevice::WriteOnly)) {
            serverInfo->setText("Could not save server settings: "+f.errorString());
            return;
        }
        f.write(QJsonDocument(serverConfig()).toJson(QJsonDocument::Indented));
        if(!f.commit())serverInfo->setText("Could not save server settings.");
        else serverInfo->setText("Settings saved locally.");
    }
    void serverRequest(QString method,QString path,QByteArray body={},bool auth=true,QString action="") {
        QUrl base(serverUrl->text().trimmed());
        QString host=base.host().toLower();
        if((base.scheme()!="https"&&!(base.scheme()=="http"&&(host=="localhost"||host=="127.0.0.1"||host=="::1")))||host.isEmpty()) {
            serverInfo->setText("Server URL must use HTTPS (HTTP is allowed only for localhost).");
            return;
        }
        QUrl url=base;
        url.setPath(base.path().remove(QRegularExpression("/+$"))+path);
        QNetworkRequest req(url);
        req.setAttribute(QNetworkRequest::RedirectPolicyAttribute,QNetworkRequest::ManualRedirectPolicy);
        req.setRawHeader("User-Agent","play-fingerprint/2");
        if(auth&&!serverKey->text().trimmed().isEmpty())req.setRawHeader("Authorization",("Bearer "+serverKey->text().trimmed()).toUtf8());
        if(!body.isEmpty())req.setHeader(QNetworkRequest::ContentTypeHeader,"application/json");
        QNetworkReply*rep=nullptr;
        if(method=="GET")rep=net->get(req);
        else if(method=="PUT")rep=net->put(req,body);
        else rep=net->sendCustomRequest(req,method.toUtf8(),body);
        serverInfo->setText("Requesting "+path+"...");
        QTimer::singleShot(60000,rep,[rep]{if(!rep->isFinished())rep->abort();});
        connect(rep,&QNetworkReply::finished,this,[this,rep,path,action]{int status=rep->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();QByteArray bytes=rep->readAll();if(rep->error()!=QNetworkReply::NoError&&status==0){if(path=="/health"&&(serverWake->isChecked()||action=="upload-ready")&&wakeAttempts<40){++wakeAttempts;serverInfo->setText(QString("Waking server... attempt %1/40 (%2)").arg(wakeAttempts).arg(rep->errorString()));QTimer::singleShot(3000,this,[this,action]{serverRequest("GET","/health",{},false,action);});}else serverInfo->setText("Server request failed: "+rep->errorString());rep->deleteLater();return;}if(action=="upload"||action=="upload-ready"||action=="download"||action=="banlist"||action=="analysis"){handleServerAction(action,status,bytes);rep->deleteLater();return;}if(status==200&&path=="/health"){wakeAttempts=0;serverInfo->setText("Server is online.");}else if(path=="/health"&&(serverWake->isChecked()||action=="upload-ready")&&wakeAttempts<40){++wakeAttempts;serverInfo->setText(QString("Waking server... attempt %1/40").arg(wakeAttempts));QTimer::singleShot(3000,this,[this,action]{serverRequest("GET","/health",{},false,action);});}else if(status==200&&path=="/api/status"){auto d=QJsonDocument::fromJson(bytes);auto o=d.object();serverInfo->setText(QString("Connected. Server holds %1 fingerprint(s) and %2 ban(s).\nHTTP 200").arg(o.value("fingerprints").toInt()).arg(o.value("bans").toInt()));}else serverInfo->setText(QString("Server replied HTTP %1: %2").arg(status).arg(QString::fromUtf8(bytes.left(400))));rep->deleteLater();});
    }
    void uploadProfiles() {
        if(PF::profileFiles().isEmpty()) {
            serverInfo->setText("No local profiles to upload.");
            return;
        }
        wakeAttempts=0;
        serverRequest("GET","/health",{},false,"upload-ready");
    }
    QStringList uploadQueue;
    void uploadNext() {
        if(uploadQueue.isEmpty()) {
            serverInfo->setText("Profile upload queue finished.");
            return;
        }
        QString file=uploadQueue.takeFirst();
        QString name=QFileInfo(file).completeBaseName();
        QFile f(PF::ProfileDir+"/"+file);
        if(!f.open(QIODevice::ReadOnly)) {
            serverInfo->setText("Cannot read profile "+name);
            uploadNext();
            return;
        }
        QUrl base(serverUrl->text().trimmed());
        if(base.scheme()!="https"&&!(base.scheme()=="http"&&(base.host()=="localhost"||base.host()=="127.0.0.1"))) {
            serverInfo->setText("Server URL must use HTTPS (except localhost).");
            return;
        }
        QUrl url=base;
        url.setPath(base.path().remove(QRegularExpression("/+$"))+"/api/fingerprints/"+QString::fromLatin1(QUrl::toPercentEncoding(name)));
        QNetworkRequest req(url);
        req.setAttribute(QNetworkRequest::RedirectPolicyAttribute,QNetworkRequest::ManualRedirectPolicy);
        req.setHeader(QNetworkRequest::ContentTypeHeader,"application/octet-stream");
        if(!serverKey->text().trimmed().isEmpty())req.setRawHeader("Authorization",("Bearer "+serverKey->text().trimmed()).toUtf8());
        auto*rep=net->put(req,f.readAll());
        connect(rep,&QNetworkReply::finished,this,[this,rep,name]{int st=rep->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();serverInfo->setText(QString("Upload %1: HTTP %2").arg(name).arg(st));rep->deleteLater();uploadNext();});
    }
    void downloadProfiles() {
        serverRequest("GET","/api/fingerprints",{},true,"download");
    }
    void pushBanList() {
        QFile f(PF::ProfileDir+"/banlist.json");
        if(!f.open(QIODevice::ReadOnly)) {
            serverInfo->setText("No local fingerprints/banlist.json found. Pull a server list first or create this JSON file.");
            return;
        }
        auto doc=QJsonDocument::fromJson(f.readAll());
        if(!doc.isObject()) {
            serverInfo->setText("Local ban list is not a JSON object.");
            return;
        }
        banQueue=doc.object().toVariantMap();
        pushBanNext();
    }
    QVariantMap banQueue;
    void pushBanNext() {
        if(banQueue.isEmpty()) {
            serverInfo->setText("Local ban-list push finished.");
            return;
        }
        QString name=banQueue.firstKey();
        QVariant entry=banQueue.take(name);
        QUrl base(serverUrl->text().trimmed());
        QString host=base.host().toLower();
        if(base.scheme()!="https"&&!(base.scheme()=="http"&&(host=="localhost"||host=="127.0.0.1"||host=="::1"))) {
            serverInfo->setText("Server URL must use HTTPS (except localhost).");
            return;
        }
        QUrl url=base;
        url.setPath(base.path().remove(QRegularExpression("/+$"))+"/api/banlist/"+QString::fromLatin1(QUrl::toPercentEncoding(name)));
        QNetworkRequest req(url);
        req.setAttribute(QNetworkRequest::RedirectPolicyAttribute,QNetworkRequest::ManualRedirectPolicy);
        req.setHeader(QNetworkRequest::ContentTypeHeader,"application/json");
        if(!serverKey->text().trimmed().isEmpty())req.setRawHeader("Authorization",("Bearer "+serverKey->text().trimmed()).toUtf8());
        auto*rep=net->put(req,QJsonDocument::fromVariant(entry).toJson(QJsonDocument::Compact));
        connect(rep,&QNetworkReply::finished,this,[this,rep,name]{int st=rep->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();serverInfo->setText(QString("Ban %1: HTTP %2").arg(name).arg(st));rep->deleteLater();pushBanNext();});
    }
    void handleServerAction(QString action,int status,QByteArray bytes) {
        if(status<200||status>=300) {
            serverInfo->setText(QString("%1 failed: HTTP %2 %3").arg(action).arg(status).arg(QString::fromUtf8(bytes.left(300))));
            return;
        }
        if(action=="upload-ready") {
            if(uploadQueue.isEmpty())uploadQueue=PF::profileFiles();
            uploadNext();
            return;
        }
        if(action=="download") {
            auto doc=QJsonDocument::fromJson(bytes);
            if(!doc.isArray()) {
                serverInfo->setText("Server returned an invalid profile list.");
                return;
            }
            downloadQueue.clear();
            downloadUpdated.clear();
            for(auto v:doc.array()) {
                auto obj=v.toObject();
                QString name=obj.value("name").toString();
                if(!name.isEmpty()) {
                    downloadQueue<<name;
                    downloadUpdated[name]=obj.value("updated").toString();
                }
            }
            downloadNext();
        }
        else if(action=="banlist") {
            QSaveFile f(PF::ProfileDir+"/banlist.json");
            if(f.open(QIODevice::WriteOnly)) {
                f.write(bytes);
                f.commit();
            }
            serverInfo->setText("Ban list downloaded and saved to AppData/fingerprint/fingerprints/banlist.json. Review before using it.");
            populateScreen();
        }
        else if(action=="analysis") {
            auto doc=QJsonDocument::fromJson(bytes);
            auto arr=doc.object().value("results").toArray();
            int flagged=0;
            QString out=QString("Server analysis returned %1 comparisons.\n").arg(arr.size());
            for(auto v:arr) {
                auto o=v.toObject();
                QString flag=o.value("flag").toString();
                if(flag=="HIGH"||flag=="REVIEW") {
                    flagged++;
                    out+=QString("%1: %2 vs %3 — distance %4\n").arg(flag).arg(o.value("candidate").toString()).arg(o.value("banned").toString()).arg(o.value("distance").toVariant().toString());
                }
            }
            out+=QString("%1 possible matches require human review. Fingerprint similarity is not proof; never auto-ban.").arg(flagged);
            serverInfo->setText(out);
        }
        else serverInfo->setText("Server action complete.");
    }
    QStringList downloadQueue;
    QMap<QString,QString> downloadUpdated;
    void downloadNext() {
        if(downloadQueue.isEmpty()) {
            refreshProfiles();
            serverInfo->setText("Download finished; profile list refreshed.");
            return;
        }
        QString name=downloadQueue.takeFirst();
        QString safeName=QFileInfo(name).fileName();
        PF::Profile local;
        if(PF::loadProfile(PF::ProfileDir+"/"+safeName+".fpr",local)&&local.updated>=downloadUpdated.value(name)) {
            QTimer::singleShot(0,this,[this]{downloadNext();});
            return;
        }
        QUrl base(serverUrl->text().trimmed());
        QUrl url=base;
        url.setPath(base.path().remove(QRegularExpression("/+$"))+"/api/fingerprints/"+QString::fromLatin1(QUrl::toPercentEncoding(name)));
        QNetworkRequest req(url);
        req.setAttribute(QNetworkRequest::RedirectPolicyAttribute,QNetworkRequest::ManualRedirectPolicy);
        if(!serverKey->text().trimmed().isEmpty())req.setRawHeader("Authorization",("Bearer "+serverKey->text().trimmed()).toUtf8());
        auto*rep=net->get(req);
        connect(rep,&QNetworkReply::finished,this,[this,rep,name,safeName]{int st=rep->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();QByteArray raw=rep->readAll();if(st==200&&raw.size()>0&&raw.size()<16*1024*1024){QSaveFile f(PF::ProfileDir+"/"+safeName+".fpr");if(f.open(QIODevice::WriteOnly)){f.write(raw);f.commit();}}rep->deleteLater();downloadNext();});
    }
    void loadIntegrationConfig() {
        stopIntegrations();
        QDir().mkpath(PF::AppDir);
        QString path=PF::AppDir+"/integrations.json";
        QFile f(path);
        if(!f.exists()) {
            QJsonObject example {
                {
                    "version",1
                }, {
                    "plugins_enabled",false
                }, {
                    "stale_after",30
                }, {
                    "integrations",QJsonObject {
                    }
                }
            };
            QSaveFile out(path);
            if(out.open(QIODevice::WriteOnly)) {
                out.write(QJsonDocument(example).toJson(QJsonDocument::Indented));
                out.commit();
            }
        }
        integrationCfg= {
        };
        if(f.open(QIODevice::ReadOnly)) {
            auto doc=QJsonDocument::fromJson(f.readAll());
            if(doc.isObject())integrationCfg=doc.object();
        }
        integrationDefs.clear();
        mapperDefs.clear();
        QDir pluginDir(PF::AppDir+"/integration_plugins");
        if(pluginDir.exists()) {
            auto files=pluginDir.entryList({"*.json"},QDir::Files,QDir::Name).mid(0,32);
            for(const auto&fn:files) {
                QFile pf(pluginDir.filePath(fn));
                if(pf.size()>65536||!pf.open(QIODevice::ReadOnly))continue;
                auto pd=QJsonDocument::fromJson(pf.readAll());
                if(pd.isObject()) {
                    auto spec=pd.object();
                    QString mn=spec.value("name").toString(QFileInfo(fn).completeBaseName());
                    if(QRegularExpression("^[A-Za-z0-9_-]{1,32}$").match(mn).hasMatch())mapperDefs[mn]=spec;
                }
            }
        }
        auto defs=integrationCfg.value("integrations").toObject();
        for(auto i=defs.begin();i!=defs.end();++i) {
            QString name=i.key();
            QJsonObject c=i.value().toObject();
            QString kind=c.value("kind").toString();
            if(!QRegularExpression("^[A-Za-z0-9_-]{1,32}$").match(name).hasMatch())continue;
            if(!QStringList{"http_push","http_poll","log_tail","udp"}.contains(kind))continue;
            integrationDefs[name]=c;
        }
        if(!net)net=new QNetworkAccessManager(this);
        if(!localNet) {
            localNet=new QNetworkAccessManager(this);
            localNet->setProxy(QNetworkProxy::NoProxy);
        }
        integrationInfo->setText(QString("Config: %1\\n%2 configured source(s). Disabled sources stay stopped. callback and Python plugin hooks are not portable to this native build.").arg(path).arg(integrationDefs.size()));
        refreshIntegrationTable();
    }
    QVariantMap mapIntegration(QString name,const QVariantMap&input) {
        QVariantMap out;
        auto c=integrationDefs.value(name);
        QString mapper=c.value("mapper").toString();
        if(mapper.isEmpty()||!mapperDefs.contains(mapper))return input;
        auto spec=mapperDefs.value(mapper);
        auto rename=spec.value("rename").toObject();
        auto drop=spec.value("drop").toArray();
        bool keep=spec.value("keep_only").toBool(false);
        for(auto it=input.begin();it!=input.end()&&out.size()<64;++it) {
            bool skip=false;
            for(auto dv:drop)if(it.key().startsWith(dv.toString())) {
                skip=true;
                break;
            }
            if(skip)continue;
            if(keep&&!rename.contains(it.key()))continue;
            QString dest=rename.contains(it.key())?rename.value(it.key()).toString():it.key();
            if(dest.isEmpty())continue;
            dest=dest.left(64);
            QVariant value=it.value();
            if(value.typeId()==QMetaType::QString)value=value.toString().left(128);
            if(value.typeId()!=QMetaType::QString&&value.typeId()!=QMetaType::Bool&&!value.canConvert<double>())continue;
            out[dest]=value;
        }
        return out;
    }
    QVariantMap decodeStruct(QByteArray data,QJsonObject cfg) {
        QVariantMap out;
        for(auto fv:cfg.value("fields").toArray()) {
            auto f=fv.toObject();
            QString key=f.value("key").toString("f"+QString::number(f.value("offset").toInt())).left(128),fmt=f.value("fmt").toString();
            int off=f.value("offset").toInt(-1);
            if(off<0)continue;
            QString type=fmt;
            type.remove(QRegularExpression("^[<>=!@]"));
            int n=(type=="b"||type=="B"||type=="?"?1:(type=="h"||type=="H"?2:(type=="i"||type=="I"||type=="l"||type=="L"||type=="f"?4:(type=="q"||type=="Q"||type=="d"?8:0))));
            if(!n||off+n>data.size())continue;
            QByteArray bytes=data.mid(off,n);
            bool little=fmt.startsWith("<")||fmt.startsWith("=")||fmt.startsWith("@");
            if((QSysInfo::ByteOrder==QSysInfo::BigEndian&&little)||(QSysInfo::ByteOrder==QSysInfo::LittleEndian&&!little&&fmt.startsWith(">"))) {
                std::reverse(bytes.begin(),bytes.end());
            }
            QDataStream ds(&bytes,QIODevice::ReadOnly);
            ds.setByteOrder(QDataStream::BigEndian);
            QVariant val;
            if(type=="f") {
                float x;
                ds>>x;
                val=x;
            }
            else if(type=="d") {
                double x;
                ds>>x;
                val=x;
            }
            else if(type=="b") {
                qint8 x;
                ds>>x;
                val=x;
            }
            else if(type=="B") {
                quint8 x;
                ds>>x;
                val=x;
            }
            else if(type=="h") {
                qint16 x;
                ds>>x;
                val=x;
            }
            else if(type=="H") {
                quint16 x;
                ds>>x;
                val=x;
            }
            else if(type=="i"||type=="l") {
                qint32 x;
                ds>>x;
                val=x;
            }
            else if(type=="I"||type=="L") {
                quint32 x;
                ds>>x;
                val=x;
            }
            else if(type=="q") {
                qint64 x;
                ds>>x;
                val=x;
            }
            else if(type=="Q") {
                quint64 x;
                ds>>x;
                val=double(x);
            }
            else if(type=="?")val=bytes[0]!=0;
            if(f.contains("scale")&&val.canConvert<double>())val=val.toDouble()*f.value("scale").toDouble(1.0);
            out[key]=val;
        }
        return out;
    }
    void startIntegrations() {
        if(!mode.isEmpty()) {
            QMessageBox::information(this,"Session running","Stop Train/Detect before starting or reloading integrations.");
            return;
        }
        for(auto it=integrationDefs.begin();it!=integrationDefs.end();++it) {
            QString name=it.key();
            QJsonObject c=it.value();
            if(!c.value("enabled").toBool(false)||integrationTimers.contains(name)||pushServers.contains(name)||udpSockets.contains(name))continue;
            QString kind=c.value("kind").toString();
            if(kind=="http_push") {
                QString token=c.value("token").toString();
                if(token.size()<8) {
                    integrationValues[name]["status"]="error: token must be 8+ chars";
                    continue;
                }
                auto*srv=new QTcpServer(this);
                quint16 port=quint16(std::clamp(c.value("port").toInt(0),0,65535));
                if(!srv->listen(QHostAddress::LocalHost,port)) {
                    integrationValues[name]["status"]="error: "+srv->errorString();
                    srv->deleteLater();
                    continue;
                }
                connect(srv,&QTcpServer::newConnection,this,[this,srv,name,token,c]{while(srv->hasPendingConnections()){QTcpSocket*sock=srv->nextPendingConnection();connect(sock,&QTcpSocket::readyRead,this,[this,sock,name,token,c]{QByteArray b=sock->property("pf_buffer").toByteArray()+sock->readAll();sock->setProperty("pf_buffer",b);int split=b.indexOf("\r\n\r\n");if(split<0)return;QByteArray head=b.left(split),body=b.mid(split+4);QRegularExpression cl("(?im)^Content-Length:\s*(\d+)");auto cm=cl.match(QString::fromUtf8(head));int n=cm.hasMatch()?cm.captured(1).toInt():-1;if(n<0||n>std::clamp(c.value("max_body").toInt(65536),256,262144)){sock->disconnectFromHost();return;}if(body.size()<n)return;body=body.left(n);int code=400;QRegularExpression auth("(?im)^Authorization:\\s*Bearer\\s+(.+)\\s*$"),xauth("(?im)^X-Integration-Token:\\s*(.+)\\s*$");auto am=auth.match(QString::fromUtf8(head));auto xm=xauth.match(QString::fromUtf8(head));bool ok=(am.hasMatch()&&am.captured(1).trimmed()==token)||(xm.hasMatch()&&xm.captured(1).trimmed()==token);QJsonParseError pe;auto doc=QJsonDocument::fromJson(body,&pe);if(!ok&&pe.error==QJsonParseError::NoError&&doc.isObject())ok=doc.object().value("auth").toObject().value("token").toString()==token;if(ok&&pe.error==QJsonParseError::NoError&&doc.isObject()){QVariantMap vals=flattenJson(doc.object());for(auto k:vals.keys())if(k=="auth"||k.startsWith("auth."))vals.remove(k);integrationValues[name]=mapIntegration(name,vals);integrationLast[name]=QDateTime::currentMSecsSinceEpoch();code=200;}else if(!ok)code=401;QByteArray response=QByteArray("HTTP/1.1 ")+QByteArray::number(code)+(code==200?" OK\r\n":" Bad Request\r\n")+"Content-Length: 0\r\nConnection: close\r\n\r\n";sock->write(response);sock->disconnectFromHost();});connect(sock,&QTcpSocket::disconnected,sock,&QObject::deleteLater);}});
                pushServers[name]=srv;
                integrationValues[name]["status"]=QString("listening 127.0.0.1:%1").arg(srv->serverPort());
            }
            else if(kind=="udp") {
                auto*u=new QUdpSocket(this);
                quint16 port=quint16(std::clamp(c.value("port").toInt(0),0,65535));
                if(!u->bind(QHostAddress::LocalHost,port)) {
                    integrationValues[name]["status"]="error: "+u->errorString();
                    u->deleteLater();
                    continue;
                }
                connect(u,&QUdpSocket::readyRead,this,[this,u,name,c]{while(u->hasPendingDatagrams()){QByteArray b; b.resize(int(u->pendingDatagramSize()));u->readDatagram(b.data(),b.size());qint64 now=QDateTime::currentMSecsSinceEpoch();if(now-udpRateEpoch.value(name,0)>=1000){udpRateEpoch[name]=now;udpRateCount[name]=0;}if(++udpRateCount[name]>std::clamp(c.value("max_rate").toInt(500),1,5000))continue;if(b.size()>std::clamp(c.value("max_packet").toInt(2048),16,65507))continue;QString decoder=c.value("decoder").toString("json");QVariantMap vals;if(decoder=="json"){auto d=QJsonDocument::fromJson(b);if(d.isObject())vals=flattenJson(d.object());}else if(decoder=="struct"){vals=decodeStruct(b,c);}else if(decoder=="text_kv"){for(QString line:QString::fromUtf8(b).split('\n')){int p=line.indexOf('=');if(p>0)vals[line.left(p).trimmed()]=autoValue(line.mid(p+1));}}if(!vals.isEmpty()){auto mapped=mapIntegration(name,vals);for(auto mi=mapped.begin();mi!=mapped.end();++mi)integrationValues[name].insert(mi.key(),mi.value());integrationLast[name]=QDateTime::currentMSecsSinceEpoch();}}});
                udpSockets[name]=u;
                integrationValues[name]["status"]=QString("UDP 127.0.0.1:%1").arg(u->localPort());
            }
            else if(kind=="http_poll") {
                QUrl url(c.value("url").toString());
                if((url.scheme()!="http"&&url.scheme()!="https")||(url.host()!="127.0.0.1"&&url.host()!="localhost"&&url.host()!="::1")) {
                    integrationValues[name]["status"]="error: poll URL must be localhost";
                    continue;
                }
                auto*t=new QTimer(this);
                connect(t,&QTimer::timeout,this,[this,t,name,url,c]{QNetworkRequest req(url);req.setAttribute(QNetworkRequest::RedirectPolicyAttribute,QNetworkRequest::ManualRedirectPolicy);auto headers=c.value("headers").toObject();for(auto hi=headers.begin();hi!=headers.end();++hi)req.setRawHeader(hi.key().left(64).toUtf8(),hi.value().toString().left(256).toUtf8());req.setRawHeader("User-Agent","play-fingerprint/2");auto*r=localNet->get(req);QTimer::singleShot(std::clamp(int(c.value("timeout").toDouble(3.0)*1000.0),500,10000),r,[r]{if(!r->isFinished())r->abort();});connect(r,&QNetworkReply::finished,this,[this,r,name,c]{QByteArray b=r->readAll();if(r->error()==QNetworkReply::NoError&&b.size()<=std::clamp(c.value("max_bytes").toInt(262144),1024,262144)){auto d=QJsonDocument::fromJson(b);if(d.isObject()){integrationValues[name]=mapIntegration(name,flattenJson(d.object()));integrationLast[name]=QDateTime::currentMSecsSinceEpoch();}}else integrationValues[name]["last error"]=r->errorString();r->deleteLater();});});
                t->start(std::clamp(int(c.value("interval").toDouble(1.0)*1000.0),250,3600000));
                integrationTimers[name]=t;
                integrationValues[name]["status"]="polling localhost";
            }
            else if(kind=="log_tail") {
                QString path=c.value("path").toString();
                if(path.isEmpty()) {
                    integrationValues[name]["status"]="error: missing path";
                    continue;
                }
                auto*t=new QTimer(this);
                auto*file=new QFile(path);
                if(!file->open(QIODevice::ReadOnly)) {
                    integrationValues[name]["status"]="error: "+file->errorString();
                    file->deleteLater();
                    continue;
                }
                if(!c.value("from_start").toBool(false))file->seek(file->size());
                tailPositions[name]=file->pos();
                tailFiles[name]=file;
                tailBuffers[name]= {
                };
                connect(t,&QTimer::timeout,this,[this,t,name,c,file]{if(!file->isOpen())return;if(file->size()<tailPositions.value(name)){file->seek(0);tailPositions[name]=0;}file->seek(tailPositions.value(name));QByteArray chunk=file->read(262144);tailPositions[name]=file->pos();QByteArray combined=tailBuffers.value(name)+chunk;QList<QByteArray> lines=combined.split('\n');tailBuffers[name]=lines.isEmpty()?QByteArray():lines.takeLast();if(tailBuffers[name].size()>4096)tailBuffers[name]=tailBuffers[name].right(4096);if(lines.size()>500)lines=lines.mid(lines.size()-500);QVariantMap vals;for(const auto&rawLine:lines){QString line=QString::fromUtf8(rawLine.left(2000));for(const auto&patv:c.value("patterns").toArray()){auto pat=patv.toObject();QRegularExpression rx(pat.value("regex").toString());auto match=rx.match(line);if(match.hasMatch()){int group=pat.value("group").toInt(1);QString val=match.captured(group);auto mp=pat.value("map").toObject();vals[pat.value("key").toString()]=mp.contains(val)?QVariant(mp.value(val).toVariant()):autoValue(val);}}}if(!vals.isEmpty()){auto mapped=mapIntegration(name,vals);for(auto mi=mapped.begin();mi!=mapped.end();++mi)integrationValues[name].insert(mi.key(),mi.value());integrationLast[name]=QDateTime::currentMSecsSinceEpoch();}});
                t->start(std::clamp(int(c.value("interval").toDouble(0.5)*1000.0),100,60000));
                integrationTimers[name]=t;
                integrationValues[name]["status"]="tailing documented log";
            }
        }
        refreshIntegrationTable();
    }
    void stopIntegrations() {
        for(auto i=integrationTimers.begin();i!=integrationTimers.end();++i) {
            i.value()->stop();
            i.value()->deleteLater();
        }
        integrationTimers.clear();
        for(auto i=pushServers.begin();i!=pushServers.end();++i) {
            i.value()->close();
            i.value()->deleteLater();
        }
        pushServers.clear();
        for(auto i=udpSockets.begin();i!=udpSockets.end();++i) {
            i.value()->close();
            i.value()->deleteLater();
        }
        udpSockets.clear();
        for(auto i=tailFiles.begin();i!=tailFiles.end();++i) {
            i.value()->close();
            i.value()->deleteLater();
        }
        tailFiles.clear();
        tailPositions.clear();
        tailBuffers.clear();
        integrationValues.clear();
        integrationLast.clear();
        udpRateEpoch.clear();
        udpRateCount.clear();
        refreshIntegrationTable();
    }
    QVariant autoValue(QString s) {
        s=s.trimmed();
        if(s.compare("true",Qt::CaseInsensitive)==0)return true;
        if(s.compare("false",Qt::CaseInsensitive)==0)return false;
        bool ok=false;
        qlonglong i=s.toLongLong(&ok);
        if(ok)return i;
        double d=s.toDouble(&ok);
        if(ok&&std::isfinite(d))return d;
        return s.left(512);
    }
    QVariantMap flattenJson(const QJsonObject&o,QString prefix="",int depth=0) {
        QVariantMap out;
        if(depth>6)return out;
        for(auto it=o.begin();it!=o.end()&&out.size()<64;++it) {
            QString k=it.key().left(64),key=(prefix.isEmpty()?k:prefix+"."+k).left(64);
            auto v=it.value();
            if(v.isObject()) {
                auto sub=flattenJson(v.toObject(),key,depth+1);
                for(auto si=sub.begin();si!=sub.end()&&out.size()<64;++si)out[si.key()]=si.value();
            }
            else if(v.isArray()) {
                auto a=v.toArray();
                for(int j=0;j<a.size()&&out.size()<64;++j) {
                    QString ak=(key+"."+QString::number(j)).left(64);
                    if(a[j].isString())out[ak]=a[j].toString().left(128);
                    else if(a[j].isDouble()||a[j].isBool())out[ak]=a[j].toVariant();
                }
            }
            else if(v.isString())out[key]=v.toString().left(128);
            else if(v.isDouble()||v.isBool())out[key]=v.toVariant();
        }
        return out;
    }
    void refreshIntegrationTable() {
        if(!integrationTable)return;
        integrationTable->setRowCount(0);
        for(auto it=integrationDefs.begin();it!=integrationDefs.end();++it) {
            QString n=it.key();
            auto c=it.value();
            int r=integrationTable->rowCount();
            integrationTable->insertRow(r);
            integrationTable->setItem(r,0,new QTableWidgetItem(n));
            integrationTable->setItem(r,1,new QTableWidgetItem(c.value("kind").toString()));
            integrationTable->setItem(r,2,new QTableWidgetItem(c.value("enabled").toBool()?"yes":"no"));
            QString state=(integrationValues.value(n).value("status").toString().isEmpty()?(c.value("enabled").toBool()?QString("stopped"):QString("disabled")):integrationValues.value(n).value("status").toString());
            if(pushServers.contains(n))state=QString("listening 127.0.0.1:%1").arg(pushServers[n]->serverPort());
            else if(udpSockets.contains(n))state=QString("UDP 127.0.0.1:%1").arg(udpSockets[n]->localPort());
            else if(integrationTimers.contains(n))state=c.value("kind").toString()=="http_poll"?"polling localhost":"tailing log";
            if(integrationLast.contains(n)) {
                qint64 age=QDateTime::currentMSecsSinceEpoch()-integrationLast[n];
                if(age>c.value("stale_after").toInt(30)*1000)state+=" (stale)";
            }
            integrationTable->setItem(r,3,new QTableWidgetItem(state));
            QStringList bits;
            auto vals=integrationValues.value(n);
            for(auto v=vals.begin();v!=vals.end();++v)if(v.key()!="status"&&v.key()!="last error")bits<<v.key()+"="+v.value().toString();
            if(vals.contains("last error"))bits<<"error: "+vals.value("last error").toString();
            integrationTable->setItem(r,4,new QTableWidgetItem(bits.join("; ").left(600)));
        }
    }
    QVariantMap contextValues() {
        QVariantMap all;
        const qint64 now=QDateTime::currentMSecsSinceEpoch();
        for(auto it=integrationValues.begin();it!=integrationValues.end();++it) {
            QString name=it.key();
            qint64 last=integrationLast.value(name,0);
            double stale=integrationDefs.value(name).value("stale_after").toDouble(integrationCfg.value("stale_after").toDouble(30));
            if(!last||now-last>stale*1000)continue;
            for(auto v=it.value().begin();v!=it.value().end();++v)if(v.key()!="status"&&v.key()!="last error")all[name+"."+v.key()]=v.value();
        }
        return all;
    }
    QVariant condEval(const QJsonObject&c,const QVariantMap&values,int depth=0) {
        if(depth>4)return  {
        };
        if(c.contains("all")||c.contains("any")) {
            bool isAll=c.contains("all");
            auto a=c.value(isAll?"all":"any").toArray();
            bool unknown=false;
            for(auto v:a) {
                QVariant r=condEval(v.toObject(),values,depth+1);
                if(!r.isValid()) {
                    unknown=true;
                    continue;
                }
                bool b=r.toBool();
                if(isAll&&!b)return false;
                if(!isAll&&b)return true;
            }
            if(unknown)return  {
            };
            return isAll;
        }
        if(c.contains("not")) {
            QVariant r=condEval(c.value("not").toObject(),values,depth+1);
            return r.isValid()?QVariant(!r.toBool()):QVariant();
        }
        QString ref=c.value("ref").toString(),op=c.value("op").toString("==");
        if(op=="exists")return values.contains(ref);
        if(op=="missing")return !values.contains(ref);
        if(!values.contains(ref))return  {
        };
        QVariant have=values.value(ref),want=c.value("value").toVariant();
        auto eq=[](QVariant a,QVariant b) {
            if(a.metaType().id()==QMetaType::Bool||b.metaType().id()==QMetaType::Bool)return a.toString().compare(b.toString(),Qt::CaseInsensitive)==0;
            bool x=false,y=false;
            double da=a.toDouble(&x),db=b.toDouble(&y);
            return x&&y?da==db:a.toString()==b.toString();
        };
        bool ok1=false,ok2=false;
        double a=have.toDouble(&ok1),b=want.toDouble(&ok2);
        if(op=="=="||op=="eq")return eq(have,want);
        if(op=="!=")return !eq(have,want);
        if(op==">"&&ok1&&ok2)return a>b;
        if(op==">="&&ok1&&ok2)return a>=b;
        if(op=="<"&&ok1&&ok2)return a<b;
        if(op=="<="&&ok1&&ok2)return a<=b;
        if(op=="contains")return have.toString().contains(want.toString(),Qt::CaseInsensitive);
        if(op=="regex") {
            QRegularExpression rx(want.toString());
            if(!rx.isValid())return  {
            };
            return rx.match(have.toString()).hasMatch();
        }
        if(op=="in"||op=="not_in") {
            bool hit=false;
            for(auto v:c.value("value").toArray())hit|=eq(have,v.toVariant());
            return op=="in"?hit:!hit;
        }
        return  {
        };
    }
    bool integrationAfk() {
        if(pushServers.isEmpty()&&udpSockets.isEmpty()&&integrationTimers.isEmpty())return false;
        auto values=contextValues();
        for(auto rv:integrationCfg.value("afk_rules").toArray()) {
            auto r=rv.toObject();
            if(r.value("enabled").isBool()&&!r.value("enabled").toBool())continue;
            QVariant w=condEval(r.value("when").toObject(),values);
            if(w.isValid()&&!w.toBool())continue;
            QVariant u=r.contains("unless")?condEval(r.value("unless").toObject(),values):QVariant(false);
            if(u.isValid()&&u.toBool())continue;
            if(w.isValid()&&w.toBool()&&u.isValid()&&!u.toBool())return true;
            if((!w.isValid()||!u.isValid())&&r.value("on_unknown").toString()=="afk")return true;
        }
        return false;
    }
    QString currentContextTag() {
        auto values=contextValues();
        QStringList parts;
        int count=0;
        for(auto tv:integrationCfg.value("tags").toArray()) {
            if(count++>=8)break;
            auto d=tv.toObject();
            QString ref=d.value("ref").toString(),name=d.value("name").toString("tag");
            if(!values.contains(ref)) {
                if(!d.value("default").isString())continue;
                parts<<name+"="+d.value("default").toString();
                continue;
            }
            QVariant val=values.value(ref);
            QString tag;
            auto mp=d.value("map").toObject();
            QString raw=val.toString();
            if(mp.contains(raw))tag=mp.value(raw).toString();
            else if(!mp.isEmpty())tag=d.value("other").toString("other");
            else {
                auto bins=d.value("bins").toArray();
                if(!bins.isEmpty()) {
                    bool ok=false;
                    double x=val.toDouble(&ok);
                    if(!ok)continue;
                    QVector<double>b;
                    for(auto q:bins)b<<q.toDouble();
                    std::sort(b.begin(),b.end());
                    int i=0;
                    while(i<b.size()&&x>=b[i])++i;
                    if(i==0)tag=QString("lt%1").arg(b[0]);
                    else if(i==b.size())tag=QString("ge%1").arg(b.last());
                    else tag=QString("%1to%2").arg(b[i-1]).arg(b[i]);
                }
                else tag=raw;
            }
            raw.replace(QRegularExpression("[^A-Za-z0-9_.+-]+"),"_");
            tag.replace(QRegularExpression("[^A-Za-z0-9_.+-]+"),"_");
            parts<<name.left(16)+"="+tag.left(24);
        }
        return parts.join("|").left(64);
    }
    QString dominantContextTag() {
        double now=PF::nowSec();
        QMap<QString,int>counts;
        int total=0;
        for(const auto&sample:contextTagSamples)if(sample.first>=now-PF::WindowSec) {
            counts[sample.second]++;
            total++;
        }
        if(!total)return  {
        };
        QString best;
        int n=0;
        for(auto i=counts.begin();i!=counts.end();++i)if(i.value()>n) {
            best=i.key();
            n=i.value();
        }
        return n/double(total)>=.8?best:QString();
    }
    QJsonObject currentContextNums() {
        QJsonObject out;
        auto values=contextValues();
        int count=0;
        for(auto nv:integrationCfg.value("numeric").toArray()) {
            if(count++>=8)break;
            auto d=nv.toObject();
            QString ref=d.value("ref").toString(),name=d.value("name").toString("num");
            name.replace(QRegularExpression("[^A-Za-z0-9_-]+"),"_");
            name=name.left(32);
            if(!values.contains(ref))continue;
            bool ok=false;
            double x=values.value(ref).toDouble(&ok);
            if(ok&&std::isfinite(x))out.insert(name.left(32),std::round(x*10000.0)/10000.0);
        }
        return out;
    }
    void toggleAfk() {
        if(mode=="train") {
            manualAfk=!manualAfk;
            afkManual->setChecked(manualAfk);
            trainInfo->setText(manualAfk?"AFK marked manually; windows should not be collected while away.":"AFK marking cleared.");
        }
    }
    protected:void closeEvent(QCloseEvent*e)override {
        if(!mode.isEmpty())stopSession();
        e->accept();
    }
};
int main(int argc,char**argv) {
    QApplication app(argc,argv);
    app.setApplicationName("Play Fingerprint");
    MainWindow w;
    w.show();
    return app.exec();
}
#include "main.moc"
