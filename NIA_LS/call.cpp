//
//  call.cpp
//  lia_ls
//
//  Created by DouglasLee on 2021/11/2.
//

#include "call.h"
#include "nia_linear_initialize.h"
#include "nia_lia_sls.h"
#include<map>
#include<limits>
#include <cmath>
using namespace std;
static std::map<std::string, int64_t> restored_md;
namespace call{
Call_fun::Call_fun():m_ls(nullptr){}

int Call_fun::func(int argc,char *argv[]){
    const bool help=argc==2 && std::string(argv[1])=="--help";
    if(argc<2 || help){
        (help?std::cout:std::cerr)
            <<"usage: nia_ls <input> [--initialize=on|off] "
              "[--restart-repair=on|off] [--seed=N] [--model-output=path]\n"
              "       [--strategy=auto|corewarm2|distancewarm|corebip|diverse|plain|novel|stepnovel]\n"
              "       [--native-seconds=N] [--core-jitter=N] [--warm-candidate=path]\n"
              "       [--core-perturb=on|off (corewarm2 only)]\n"
              "       [--distance-scoring=on|off] [--joint-operators=on|off]\n"
              "       (auto: first auxiliary stage only; corewarm2: this search)\n"
              "       [--second-stage=on|off (auto only; default on)]\n"
              "       [--stepnovel-choice=softmax|break|rank|random (auto/stepnovel only; default softmax)]\n"
              "       [--stepnovel-temperature=T (softmax initial temperature; 0.1 <= T <= 2; default 0.25)]\n"
              "       [--stepnovel-window=N (softmax stagnation window in search steps; default 20000)]\n"
              "       [--stepnovel-beta=B (break sampling; 0 < B <= 8; default 2)]\n"
              "       (stepnovel parameters: auto/stepnovel only)\n";
        delete m_ls;
        return help?0:2;
    }
    nia::SlsStrategy strategy=nia::SlsStrategy::automatic;
    for(int arg=2;arg<argc;++arg){
        const std::string option=argv[arg];
        if(option.rfind("--strategy=",0)==0 && !nia::parse_strategy(option.substr(11),strategy)){
            std::cerr<<"invalid strategy: "<<option.substr(11)<<"\n";return 2;
        }
    }
    m_ls=new nia::ls_solver(0,INT64_MAX,true,false,strategy);
    nia::lia_seed=0;
    bool initialize_enabled=true;
    m_ls->restart_repair_enabled=true;
    std::string model_output,warm_input;
    double native_seconds=1200;
    for(int arg=2;arg<argc;++arg){
        const std::string option=argv[arg];
        if(option.rfind("--strategy=",0)==0)continue;
        if(option=="--initialize=on")initialize_enabled=true;
        else if(option=="--initialize=off")initialize_enabled=false;
        else if(option=="--restart-repair=on")m_ls->restart_repair_enabled=true;
        else if(option=="--restart-repair=off")m_ls->restart_repair_enabled=false;
        else if(option.rfind("--seed=",0)==0){
            try{
                const std::string value=option.substr(7);
                if(value.empty() || !std::all_of(value.begin(),value.end(),[](char ch){return ch>='0' && ch<='9';}))
                    throw std::invalid_argument("seed");
                unsigned long seed=std::stoul(value);
                if(seed>std::numeric_limits<unsigned>::max())throw std::out_of_range("seed");
                nia::lia_seed=static_cast<unsigned>(seed);m_ls->mt.seed(nia::lia_seed);
            }catch(...){std::cerr<<"invalid seed\n";delete m_ls;return 2;}
        }
        else if(option.rfind("--core-jitter=",0)==0 && (strategy==nia::SlsStrategy::corewarm2 || strategy==nia::SlsStrategy::corebip)){
            try{
                const auto value=option.substr(14);size_t used=0;unsigned long jitter=std::stoul(value,&used);
                if(used!=value.size() || value.empty() || value[0]=='-')throw std::invalid_argument("jitter");
                m_ls->core_jitter=unsigned(std::min(16ul,jitter));
                m_ls->restart_stall_limit=strategy==nia::SlsStrategy::corebip?30000:50000;
                m_ls->restart_repair_enabled=false;
            }catch(...){std::cerr<<"invalid core jitter\n";delete m_ls;return 2;}
        }
        else if(strategy==nia::SlsStrategy::corewarm2 && (option=="--core-perturb=on" || option=="--core-perturb=off"))
            m_ls->core_perturb_enabled=option=="--core-perturb=on";
        else if(strategy==nia::SlsStrategy::automatic && (option=="--second-stage=on" || option=="--second-stage=off"))
            m_ls->second_stage_enabled=option=="--second-stage=on";
        else if((strategy==nia::SlsStrategy::automatic || strategy==nia::SlsStrategy::stepnovel) &&
                option.rfind("--stepnovel-choice=",0)==0){
            if(!nia::parse_stepnovel_choice(option.substr(19),m_ls->stepnovel_choice)){
                std::cerr<<"invalid stepnovel choice\n";delete m_ls;return 2;
            }
        }
        else if((strategy==nia::SlsStrategy::automatic || strategy==nia::SlsStrategy::stepnovel) &&
                option.rfind("--stepnovel-temperature=",0)==0){
            try{
                const auto value=option.substr(24);size_t used=0;const double temperature=std::stod(value,&used);
                if(used!=value.size() || !nia::AdaptiveSoftmax::valid_temperature(temperature))throw std::invalid_argument("temperature");
                m_ls->stepnovel_softmax.initial_temperature=temperature;
            }catch(...){std::cerr<<"invalid stepnovel temperature: require 0.1 <= T <= 2\n";delete m_ls;return 2;}
        }
        else if((strategy==nia::SlsStrategy::automatic || strategy==nia::SlsStrategy::stepnovel) &&
                option.rfind("--stepnovel-window=",0)==0){
            try{
                const auto value=option.substr(19);
                if(value.empty() || !std::all_of(value.begin(),value.end(),[](char c){return c>='0' && c<='9';}))throw std::invalid_argument("window");
                const auto window=std::stoull(value);
                if(!window || window>std::numeric_limits<uint64_t>::max())throw std::invalid_argument("window");
                m_ls->stepnovel_softmax.window=window;
            }catch(...){std::cerr<<"invalid stepnovel window: require a positive uint64 integer\n";delete m_ls;return 2;}
        }
        else if((strategy==nia::SlsStrategy::automatic || strategy==nia::SlsStrategy::stepnovel) &&
                option.rfind("--stepnovel-beta=",0)==0){
            try{
                const auto value=option.substr(17);size_t used=0;const double beta=std::stod(value,&used);
                if(used!=value.size() || !nia::valid_stepnovel_beta(beta))throw std::invalid_argument("beta");
                m_ls->stepnovel_beta=beta;
            }catch(...){std::cerr<<"invalid stepnovel beta: require 0 < beta <= 8\n";delete m_ls;return 2;}
        }
        else if((strategy==nia::SlsStrategy::automatic || strategy==nia::SlsStrategy::corewarm2) &&
                (option=="--distance-scoring=on" || option=="--distance-scoring=off")){
            if(strategy==nia::SlsStrategy::automatic)m_ls->first_stage_distance_scoring=option=="--distance-scoring=on";
            else m_ls->distance_scoring_enabled=option=="--distance-scoring=on";
        }
        else if((strategy==nia::SlsStrategy::automatic || strategy==nia::SlsStrategy::corewarm2) &&
                (option=="--joint-operators=on" || option=="--joint-operators=off")){
            if(strategy==nia::SlsStrategy::automatic)m_ls->first_stage_joint_operators=option=="--joint-operators=on";
            else m_ls->joint_operators_enabled=option=="--joint-operators=on";
        }
        else if(option.rfind("--warm-candidate=",0)==0 && m_ls->config.gate_input)warm_input=option.substr(17);
        else if(option.rfind("--native-seconds=",0)==0){
            try{
                const auto value=option.substr(17);size_t used=0;native_seconds=std::stod(value,&used);
                if(used!=value.size() || !std::isfinite(native_seconds) || native_seconds<=0)throw std::invalid_argument("seconds");
            }catch(...){std::cerr<<"invalid time budget\n";delete m_ls;return 2;}
        }

        else if(option.rfind("--model-output=",0)==0 && option.size()>15)model_output=option.substr(15);
        else{
            std::cerr<<"unknown option: "<<option<<"\n";
            delete m_ls;
            return 2;
        }
    }
    auto write_model=[&](const std::map<std::string,std::string>& values){
        if(model_output.empty())return true;
        std::ofstream out(model_output);
        for(const auto& entry:values)out<<entry.first<<'\t'<<entry.second<<'\n';
        if(!out){std::cerr<<"cannot write model\n";return false;}
        return true;
    };
    m_ls->_cutoff=native_seconds;
    if(strategy==nia::SlsStrategy::automatic)m_ls->native_repair_input=argv[1];
    if(!warm_input.empty()){
        std::ifstream warm(warm_input);std::string row;
        if(!warm){std::cerr<<"cannot read warm candidate\n";delete m_ls;return 2;}
        while(std::getline(warm,row)){auto tab=row.find('\t');if(tab!=std::string::npos)m_ls->progressive_values[row.substr(0,tab)]=row.substr(tab+1);}
        if(!warm.eof()){delete m_ls;return 2;}
        m_ls->force_progressive=true;initialize_enabled=false;
    }
    std::cerr<<"sls_strategy="<<nia::strategy_name(strategy)<<"\n";
    auto log_stepnovel_parameters=[&](){
        if(m_ls->stepnovel_choice==nia::StepnovelChoice::softmax)
            std::cerr<<" temperature="<<m_ls->stepnovel_softmax.initial_temperature<<" window="<<m_ls->stepnovel_softmax.window
                     <<" min_temperature="<<nia::AdaptiveSoftmax::minimum<<" max_temperature="<<nia::AdaptiveSoftmax::maximum;
        else if(m_ls->stepnovel_choice==nia::StepnovelChoice::break_cost)std::cerr<<" beta="<<m_ls->stepnovel_beta;
    };
    if(strategy==nia::SlsStrategy::automatic){
        std::cerr<<"first_stage_options distance_scoring="<<(m_ls->first_stage_distance_scoring?"on":"off")
                 <<" joint_operators="<<(m_ls->first_stage_joint_operators?"on":"off")<<"\n";
        std::cerr<<"auxiliary_stages first=on second="<<(m_ls->second_stage_enabled?"on":"off")<<"\n";
        std::cerr<<"second_stage_options selection="<<nia::stepnovel_choice_name(m_ls->stepnovel_choice);
        log_stepnovel_parameters();std::cerr<<"\n";
    }
    if(strategy==nia::SlsStrategy::stepnovel){
        std::cerr<<"stepnovel_selection="<<nia::stepnovel_choice_name(m_ls->stepnovel_choice);
        log_stepnovel_parameters();std::cerr<<" restart_stall="<<m_ls->restart_stall_limit<<"\n";
    }
    if(strategy==nia::SlsStrategy::corewarm2)
        std::cerr<<"search_options distance_scoring="<<(m_ls->use_distance_scoring()?"on":"off")
                 <<" joint_operators="<<(m_ls->joint_operators_enabled?"on":"off")<<"\n";
    if(!m_ls->core_perturb_enabled)
        std::cerr<<"core_perturb=off core_jitter="<<m_ls->core_jitter
                 <<" restart_stall="<<m_ls->restart_stall_limit
                 <<" restart_repair="<<m_ls->restart_repair_enabled<<"\n";

    std::cerr<<"lia_backend=sls\n";
    if(initialize_enabled){
        nia::initial_model initial;
        if(nia::make_progressive_initial_model(argv[1],10.0,initial,strategy)){
            std::cerr<<"pipeline_stage=full\n";
            std::cerr<<"linear_initialization=validated seconds="<<initial.seconds
                     <<" bounded_variables="<<initial.bounded_variables<<"\n";
            if(!write_model(initial.values)){delete m_ls;return 2;}
            std::cout<<"sat"<<std::endl;
            delete m_ls;
            return 0;
        }
        m_ls->progressive_values=std::move(initial.values);
        std::cerr<<"pipeline_stage=local saved_candidate="<<(!m_ls->progressive_values.empty())<<"\n";
    }
    string in_string;
    uint64_t num_lits;
    freopen(argv[1],"r",stdin);
    cin>>num_lits;
    m_ls->make_lits_space(num_lits);//此处需要设定lits数量
    getline(cin,in_string);
    getline(cin, in_string);
    unsigned gate_literals=0;
    while(in_string!="0"){
        if(strategy==nia::SlsStrategy::automatic){
            if(in_string.find(" ite ")!=std::string::npos || in_string.find(" iff ")!=std::string::npos)++gate_literals;
            for(size_t j=0;j<in_string.size();++j){
                if((j==0 || std::isspace(static_cast<unsigned char>(in_string[j-1]))) && (std::isdigit(static_cast<unsigned char>(in_string[j])) || in_string[j]=='-')){
                    size_t k=j+(in_string[j]=='-');uint64_t number=0;while(k<in_string.size() && std::isdigit(static_cast<unsigned char>(in_string[k]))){number=std::min(uint64_t(1000000000000ULL),number*10+unsigned(in_string[k]-'0'));++k;}
                    if(number>10000000 && (k==in_string.size() || std::isspace(static_cast<unsigned char>(in_string[k])) || in_string[k]==')'))m_ls->input_large=true;
                    j=k>j?k-1:j;
                }
            }
        }
        m_ls->build_lits(in_string);//传入string到lits
        getline(cin,in_string);
    }
    int size;
    cin>>size;
    std::vector<std::vector<int> > vec;
    vec.resize(size);
    int size_now=0;
    while(size_now<size){
        cin>>in_string;
        if(in_string=="(")continue;
        else if(in_string==")"){size_now++;}
        else{vec[size_now].push_back(atoi(in_string.c_str()));}
    }
    size=0;
    cin>>size;
    for(;size>0;size--){
        std::string v_name;
        int val;
        cin>>v_name;
        cin>>val;
        restored_md[v_name]=val;
    }
    if(strategy==nia::SlsStrategy::automatic){
        unsigned units=0,max_clause=0;for(const auto& cl:vec){units+=cl.size()==1;max_clause=std::max(max_clause,unsigned(cl.size()));}
        m_ls->input_unit_dense=uint64_t(units)*10>=uint64_t(vec.size())*9;
        m_ls->input_gate_dense=uint64_t(gate_literals)*2>num_lits;
        m_ls->input_binary=max_clause<=2 && vec.size()>100000;
        m_ls->input_small=vec.size()<200;
        m_ls->input_wide_clause=max_clause>512;
        m_ls->input_sparse=uint64_t(vec.size())<4*num_lits && max_clause<=128 && !m_ls->input_unit_dense && !m_ls->input_gate_dense && !m_ls->input_large && !m_ls->input_binary && !m_ls->input_small;
        std::cerr<<"sls_structure unit_dense="<<m_ls->input_unit_dense<<" gate_dense="<<m_ls->input_gate_dense<<" large="<<m_ls->input_large<<" binary="<<m_ls->input_binary<<" sparse="<<m_ls->input_sparse<<" small="<<m_ls->input_small<<"\n";
    }
    m_ls->build_instance(vec);//传入一个vector<vector<int> >
    // m_ls->construct_solution();
    // m_ls->print_formula();
//    m_ls->print_formula_pbs();
    bool is_sat = m_ls->local_search();
    std::cerr<<"search_result search_sat="<<is_sat<<" steps="<<(m_ls->build_unsat?0:m_ls->_step)<<"\n";
    if(strategy==nia::SlsStrategy::stepnovel && m_ls->stepnovel_choice!=nia::StepnovelChoice::random){
        std::cerr<<"stepnovel_sampling calls="<<m_ls->sampling_calls<<" nonbest="<<m_ls->sampling_nonbest
                 <<" choice="<<nia::stepnovel_choice_name(m_ls->stepnovel_choice);
        if(m_ls->stepnovel_choice==nia::StepnovelChoice::softmax)
            std::cerr<<" temperature="<<(m_ls->stepnovel_softmax.active?m_ls->stepnovel_softmax.temperature:m_ls->stepnovel_softmax.initial_temperature)
                     <<" heat_windows="<<m_ls->stepnovel_softmax.heat_windows<<" improvements="<<m_ls->stepnovel_softmax.improvements;
        else if(m_ls->stepnovel_choice==nia::StepnovelChoice::break_cost)std::cerr<<" beta="<<m_ls->stepnovel_beta;
        std::cerr<<"\n";
    }
    if(m_ls->joint_operators_enabled)
        std::cerr<<"joint_operations attempts="<<m_ls->joint_attempts<<" candidates="<<m_ls->joint_candidates
                 <<" evaluated="<<m_ls->joint_evaluations<<" divisibility="<<m_ls->joint_divisibility_moves
                 <<" preserve_equality="<<m_ls->joint_preserving_moves<<"\n";
    if(is_sat && !model_output.empty()){
        std::map<std::string,std::string> values;
        for(const auto& var:m_ls->_vars){
            auto index=m_ls->name2var.at(var.var_name);
            values[var.var_name]=var.is_nia?m_ls->print_128(m_ls->_solution[index]):(m_ls->_solution[index]>0?"true":"false");
        }
        for(const auto& var:m_ls->_resolution_vars)
            if(!var.is_nia && !values.count(var.var_name))values[var.var_name]=var.up_bool>0?"true":"false";
        if(!m_ls->external_sat_values.empty())values=m_ls->external_sat_values;
        if(!write_model(values)){delete m_ls;return 2;}
    }
    std::cout << (is_sat ? "sat" : "unknown") << std::endl;
    // string var_value;
    // string var_name="tmp189__global__428__instrumented__";
    // m_ls->print_var_solution(var_name, var_value);
    delete m_ls;
    // std::cout<<var_name<<" : "<<var_value<<endl;
    return 0;
}
}
namespace nia {
const char* strategy_name(SlsStrategy strategy){
    switch(strategy){
    case SlsStrategy::automatic:return "auto";
    case SlsStrategy::corewarm2:return "corewarm2";
    case SlsStrategy::distancewarm:return "distancewarm";
    case SlsStrategy::corebip:return "corebip";
    case SlsStrategy::diverse:return "diverse";
    case SlsStrategy::plain:return "plain";
    case SlsStrategy::novel:return "novel";
    case SlsStrategy::stepnovel:return "stepnovel";
    }
    return "auto";
}
bool parse_strategy(const std::string& name,SlsStrategy& strategy){
    for(auto value:{SlsStrategy::automatic,SlsStrategy::corewarm2,SlsStrategy::distancewarm,SlsStrategy::corebip,
                    SlsStrategy::diverse,SlsStrategy::plain,SlsStrategy::novel,SlsStrategy::stepnovel}){
        if(name==strategy_name(value)){strategy=value;return true;}
    }
    return false;
}
}
namespace call {
int run_sls_entry(int argc,char** argv){
    restored_md.clear();
    Call_fun c;return c.func(argc,argv);
}
}
#ifndef NIA_NO_MAIN
int main(int argc,char** argv){return call::run_sls_entry(argc,argv);}
#endif
