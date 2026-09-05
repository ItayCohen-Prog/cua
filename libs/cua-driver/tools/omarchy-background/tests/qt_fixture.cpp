#include <QtWidgets>

class Probe : public QWidget {
public:
    QString path; int clicks=0, keys=0, hotkeys=0, wheel=0, rights=0, doubles=0;
    QLineEdit *text; QSlider *slider;
    Probe(QString p, QString title):path(p) {
        setWindowTitle(title); resize(620,460);
        auto *layout=new QVBoxLayout(this);
        auto *button=new QPushButton("Increment",this); button->setMinimumHeight(70);layout->addWidget(button);
        text=new QLineEdit(this);text->setPlaceholderText("Type here");text->setMinimumHeight(50);layout->addWidget(text);
        slider=new QSlider(Qt::Horizontal,this);slider->setRange(0,100);slider->setMinimumHeight(50);layout->addWidget(slider);
        auto *label=new QLabel("F5 / Ctrl+Shift+K / scroll / right-click / double-click",this);label->setMinimumHeight(160);layout->addWidget(label);
        connect(button,&QPushButton::clicked,this,[this]{clicks++;save();});
        connect(text,&QLineEdit::textChanged,this,[this]{save();});
        connect(slider,&QSlider::valueChanged,this,[this]{save();});
        auto *shortcut=new QShortcut(QKeySequence("Ctrl+Shift+K"),this);
        connect(shortcut,&QShortcut::activated,this,[this]{hotkeys++;save();});
        QTimer *timer=new QTimer(this);connect(timer,&QTimer::timeout,this,[this]{save();});timer->start(30);
        qApp->installEventFilter(this);
        show(); QFile idFile(path+".xid");idFile.open(QIODevice::WriteOnly);idFile.write(QByteArray::number(winId()));idFile.close(); save();
    }
    bool eventFilter(QObject *o,QEvent *e) override {
        if(e->type()==QEvent::MouseButtonPress || e->type()==QEvent::MouseButtonRelease){auto *m=static_cast<QMouseEvent*>(e);QFile f(path+".events"); f.open(QIODevice::Append); f.write(QString("%1 %2 %3,%4 global=%5,%6 button=%7\n").arg(o->metaObject()->className()).arg(int(e->type())).arg(m->pos().x()).arg(m->pos().y()).arg(m->globalPos().x()).arg(m->globalPos().y()).arg(int(m->button())).toUtf8());}
        return QWidget::eventFilter(o,e);
    }
    void save(){QJsonObject state{{"pid",qint64(QCoreApplication::applicationPid())},{"clicks",clicks},{"text",text->text()},{"slider",slider->value()},{"keys",keys},{"hotkeys",hotkeys},{"wheel",wheel},{"rights",rights},{"doubles",doubles}};QSaveFile f(path);if(f.open(QIODevice::WriteOnly)){f.write(QJsonDocument(state).toJson());f.commit();}}
    void keyPressEvent(QKeyEvent *e) override{if(e->key()==Qt::Key_F5){keys++;save();} QWidget::keyPressEvent(e);}
    void wheelEvent(QWheelEvent *e) override{wheel+=e->angleDelta().y();save();e->accept();}
    void mousePressEvent(QMouseEvent *e) override{if(e->button()==Qt::RightButton){rights++;save();}QWidget::mousePressEvent(e);}
    void mouseDoubleClickEvent(QMouseEvent *e) override{doubles++;save();QWidget::mouseDoubleClickEvent(e);}
};
int main(int argc,char **argv){QApplication app(argc,argv);Probe probe(argv[1],argv[2]);return app.exec();}
